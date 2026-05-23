#!/usr/bin/env python3
"""
智能告警修复 - v5.2 最终修复版
修复问题:
1. 步骤2执行完成的准确判断
2. 步骤4状态检查不显示详细状态的问题
3. API查询失败时的错误处理

作者: OpenClaw
日期: 2026-03-27
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import auto_load_env
from config.config import DS_CONFIG, FUYAN_WORKFLOWS, REPAIR_CONFIG, TABLE_CONFIG, WORKSPACE_CONFIG

import json
import os
import re
import urllib.request
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

# 配置
WORKSPACE = WORKSPACE_CONFIG['root']
AUTO_REPAIR_RECORDS_DIR = WORKSPACE_CONFIG['auto_repair_records_dir']
DS_BASE = DS_CONFIG['base_url']
PROJECT_CODE = DS_CONFIG['project_code']
FUYAN_PROJECT_CODE = DS_CONFIG['fuyan_project_code']
DS_TOKEN = DS_CONFIG['token']
DS_ENVIRONMENT_CODE = DS_CONFIG['environment_code']
DS_TENANT_CODE = DS_CONFIG['tenant_code']
DS_API_MODE = DS_CONFIG.get('api_mode', 'auto')
DS_START_ENDPOINT = DS_CONFIG.get('start_endpoint', 'auto')
DS_START_CODE_FIELD = DS_CONFIG.get('start_code_field', 'auto')
DS_DEFINITION_ENDPOINT_STYLE = DS_CONFIG.get('definition_endpoint_style', 'auto')
DS_INSTANCE_ENDPOINT_STYLE = DS_CONFIG.get('instance_endpoint_style', 'auto')
QUALITY_RESULT_TABLE = TABLE_CONFIG['quality_result_table']
MANUAL_REVIEW_STATE_FILE = WORKSPACE_CONFIG['manual_review_state_file']
SCAN_LOOKBACK_DAYS = REPAIR_CONFIG['scan_lookback_days']
PRIORITY_WORKFLOWS = REPAIR_CONFIG.get('priority_workflow_codes') or []
BLOCKED_WORKFLOW_NAMES = {
    str(name).strip()
    for name in (REPAIR_CONFIG.get('blocked_workflow_names') or [])
    if str(name).strip()
}
BLOCKED_FUYAN_WORKFLOW_NAMES = {
    str(name).strip()
    for name in (REPAIR_CONFIG.get('blocked_fuyan_workflow_names') or [])
    if str(name).strip()
}
DS_STATUS_DEBUG = os.environ.get('REPAIR_DEBUG_DS_STATUS', '').strip().lower() in {'1', 'true', 'yes', 'on'}
REPAIR_WORKFLOW_CONFLICT_POLL_INTERVAL_SECONDS = int(os.environ.get('REPAIR_WORKFLOW_CONFLICT_POLL_INTERVAL_SECONDS', '30'))
REPAIR_WORKFLOW_CONFLICT_WAIT_SECONDS = int(os.environ.get('REPAIR_WORKFLOW_CONFLICT_WAIT_SECONDS', '1800'))
FAILED_STATE_CONFIRMATION_GRACE_SECONDS = int(os.environ.get('FAILED_STATE_CONFIRMATION_GRACE_SECONDS', '45'))

# 维护任务关键词（排除）
MAINTENANCE_KEYWORDS = ['补充', '删除', '清理', '修复', '历史', '冗余', '临时', 'test', 'copy', '手插入']


def debug_log(msg):
    """按需输出 DS 状态排查日志，避免默认刷屏。"""
    if DS_STATUS_DEBUG:
        log(f"[DS-DEBUG] {msg}")


def _get_definition_detail_endpoints(project_code, workflow_code):
    if DS_DEFINITION_ENDPOINT_STYLE == 'workflow-definition':
        return [
            f"/projects/{project_code}/workflow-definition/{workflow_code}",
            f"/projects/{project_code}/process-definition/{workflow_code}",
        ]
    if DS_DEFINITION_ENDPOINT_STYLE == 'process-definition':
        return [
            f"/projects/{project_code}/process-definition/{workflow_code}",
            f"/projects/{project_code}/workflow-definition/{workflow_code}",
        ]
    return [
        f"/projects/{project_code}/workflow-definition/{workflow_code}",
        f"/projects/{project_code}/process-definition/{workflow_code}",
    ]


def _get_definition_list_endpoint_templates():
    if DS_DEFINITION_ENDPOINT_STYLE == 'workflow-definition':
        return [
            "/projects/{project_code}/workflow-definition?pageNo={page_no}&pageSize=100",
            "/projects/{project_code}/workflow-definition/query-workflow-definition-list",
            "/projects/{project_code}/workflow-definition/query-process-definition-list",
            "/projects/{project_code}/process-definition?pageNo={page_no}&pageSize=100",
            "/projects/{project_code}/process-definition/query-workflow-definition-list",
            "/projects/{project_code}/process-definition/query-process-definition-list",
        ]
    if DS_DEFINITION_ENDPOINT_STYLE == 'process-definition':
        return [
            "/projects/{project_code}/process-definition?pageNo={page_no}&pageSize=100",
            "/projects/{project_code}/process-definition/query-workflow-definition-list",
            "/projects/{project_code}/process-definition/query-process-definition-list",
            "/projects/{project_code}/workflow-definition?pageNo={page_no}&pageSize=100",
            "/projects/{project_code}/workflow-definition/query-workflow-definition-list",
            "/projects/{project_code}/workflow-definition/query-process-definition-list",
        ]
    return [
        "/projects/{project_code}/workflow-definition?pageNo={page_no}&pageSize=100",
        "/projects/{project_code}/process-definition?pageNo={page_no}&pageSize=100",
        "/projects/{project_code}/workflow-definition/query-workflow-definition-list",
        "/projects/{project_code}/workflow-definition/query-process-definition-list",
        "/projects/{project_code}/process-definition/query-workflow-definition-list",
        "/projects/{project_code}/process-definition/query-process-definition-list",
    ]


def _get_instance_detail_endpoints(project_code, instance_id):
    if DS_INSTANCE_ENDPOINT_STYLE == 'workflow-instances':
        return [f"/projects/{project_code}/workflow-instances/{instance_id}"]
    if DS_INSTANCE_ENDPOINT_STYLE == 'process-instances':
        return [f"/projects/{project_code}/process-instances/{instance_id}"]
    return [
        f"/projects/{project_code}/workflow-instances/{instance_id}",
        f"/projects/{project_code}/process-instances/{instance_id}",
    ]


def _get_instance_list_styles():
    if DS_INSTANCE_ENDPOINT_STYLE == 'workflow-instances':
        return ['workflow-instances']
    if DS_INSTANCE_ENDPOINT_STYLE == 'process-instances':
        return ['process-instances']
    return ['workflow-instances', 'process-instances']


def _get_start_attempts():
    if DS_START_ENDPOINT != 'auto' or DS_START_CODE_FIELD != 'auto':
        endpoint = DS_START_ENDPOINT if DS_START_ENDPOINT != 'auto' else 'start-process-instance'
        code_field = DS_START_CODE_FIELD if DS_START_CODE_FIELD != 'auto' else 'processDefinitionCode'
        return [(endpoint, code_field)]

    if DS_API_MODE == 'workflow_v1':
        return [('start-workflow-instance', 'workflowDefinitionCode')]
    if DS_API_MODE == 'process_v2':
        return [('start-process-instance', 'processDefinitionCode')]

    return [
        ('start-process-instance', 'processDefinitionCode'),
        ('start-workflow-instance', 'workflowDefinitionCode'),
    ]


def _extract_instance_id_from_start_result(result):
    """从启动接口返回中提取实例ID；如果返回为空/无效，视为未真正启动成功。"""
    instance_data = result.get('data')
    if isinstance(instance_data, list):
        return instance_data[0] if instance_data else None
    return instance_data


def build_start_params_payloads(dt):
    """兼容不同 DS 集群对 startParams 的 JSON 结构要求。"""
    key_value_map = {'dt': dt}
    property_list = [{'prop': 'dt', 'direct': 'IN', 'type': 'VARCHAR', 'value': dt}]
    global_wrapper = {'global': property_list}

    return [
        json.dumps(key_value_map),
        json.dumps(property_list),
        json.dumps(global_wrapper),
    ]


def should_retry_with_property_list_start_params(message):
    text = str(message or '')
    lowered = text.lower()
    return (
        'startparams' in lowered
        or 'start params' in lowered
        or 'parse json' in lowered
        or 'property failed' in lowered
        or 'property list failed' in lowered
        or 'map failed' in lowered
    )


def normalize_table_identifier(value):
    text = str(value or '').strip().lower().strip('`')
    return text.replace('`', '')


def strip_table_prefix(value):
    normalized = normalize_table_identifier(value)
    for prefix in ('dwd_', 'dwb_', 'ods_'):
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
    return normalized


def is_task_name_match(task_name, table_name):
    task_name_normalized = normalize_table_identifier(task_name)
    table_name_normalized = normalize_table_identifier(table_name)
    return table_name_normalized == task_name_normalized


def sql_targets_table(sql_text, table_name):
    sql = str(sql_text or '').lower()
    if not sql:
        return False

    expected = normalize_table_identifier(table_name)
    expected_suffix = f".{expected}"
    patterns = [
        r"\binsert\s+overwrite\s+table\s+([`a-zA-Z0-9_.]+)",
        r"\binsert\s+into\s+table\s+([`a-zA-Z0-9_.]+)",
        r"\binsert\s+into\s+([`a-zA-Z0-9_.]+)",
        r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([`a-zA-Z0-9_.]+)",
    ]

    for pattern in patterns:
        for matched in re.findall(pattern, sql):
            candidate = normalize_table_identifier(matched)
            if candidate == expected or candidate.endswith(expected_suffix):
                return True
    return False


def start_workflow_instance_with_fallbacks(project_code, workflow_code, base_data, dt=None, table=''):
    start_attempts = _get_start_attempts()
    start_params_payloads = build_start_params_payloads(dt) if dt else [None]
    success = False
    result = {}
    msg = ''
    used_endpoint = ''
    used_payload = {}
    launched_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for start_endpoint, code_field in start_attempts:
        for index, start_params_payload in enumerate(start_params_payloads):
            attempt_data = dict(base_data)
            if start_params_payload is not None:
                attempt_data['startParams'] = start_params_payload
            attempt_data[code_field] = workflow_code
            attempt_data['scheduleTime'] = launched_at if start_endpoint == 'start-workflow-instance' else ''
            used_endpoint = f"/projects/{project_code}/executors/{start_endpoint}"
            used_payload = attempt_data
            debug_log(
                f"尝试启动 table={table or workflow_code} endpoint={used_endpoint} "
                f"code_field={code_field} start_params_index={index}"
            )
            success, result, msg = ds_api_post(used_endpoint, attempt_data)
            if success:
                extracted_instance_id = _extract_instance_id_from_start_result(result)
                if extracted_instance_id not in (None, ''):
                    return True, result, msg, used_endpoint, used_payload, launched_at
                debug_log(
                    f"启动接口返回成功但无实例ID table={table or workflow_code} "
                    f"endpoint={used_endpoint} raw_data={result.get('data')!r}"
                )
                success = False
                result = {}
                msg = '启动接口返回成功但未提供实例ID'
            else:
                debug_log(
                    f"启动失败 table={table or workflow_code} endpoint={used_endpoint} "
                    f"msg={msg or result.get('msg', '')}"
                )
                if start_params_payload is not None and index == 0 and should_retry_with_property_list_start_params(msg):
                    continue
            break

    return success, result, msg, used_endpoint, used_payload, launched_at


def _get_instance_state_types_for_search(include_all=False):
    """根据接口风格选择可查询的状态枚举，避开已知不兼容口径。"""
    if DS_INSTANCE_ENDPOINT_STYLE == 'process-instances' or DS_API_MODE == 'process_v2':
        state_types = ['RUNNING_EXECUTION', 'SUCCESS', 'FAILURE', 'READY_STOP', None]
    else:
        state_types = ['RUNNING_EXECUTION', 'READY_STOP', 'FAILURE', 'SUCCESS', 'FINISHED', None]

    if include_all:
        insert_at = max(len(state_types) - 1, 0)
        state_types.insert(insert_at, 'ALL')
    return tuple(state_types)


def get_workflow_definition_detail(workflow_code, project_code=None):
    """兼容 DS 3.3 workflow-definition 与 DS 3.2 process-definition 详情接口"""
    project_code = project_code or PROJECT_CODE
    endpoints = _get_definition_detail_endpoints(project_code, workflow_code)
    last_msg = ""
    for endpoint in endpoints:
        success, detail, msg = ds_api_get(endpoint)
        if success:
            return True, detail, msg
        last_msg = msg
    return False, {}, last_msg


def get_workflow_name_from_detail(detail):
    """兼容不同 DS 版本返回结构，尽量提取工作流名称。"""
    if not isinstance(detail, dict):
        return ''

    candidates = [
        detail.get('processDefinition', {}).get('name', ''),
        detail.get('workflowDefinition', {}).get('name', ''),
        detail.get('processDefinitionName', ''),
        detail.get('workflowDefinitionName', ''),
        detail.get('name', ''),
        detail.get('workflowName', ''),
    ]
    for name in candidates:
        normalized = str(name).strip()
        if normalized:
            return normalized
    return ''


def get_workflow_definition_list():
    """兼容 DS 3.3 workflow-definition 与 DS 3.2 process-definition 列表接口，并自动翻页"""
    endpoint_templates = _get_definition_list_endpoint_templates()
    last_msg = ""

    for endpoint_template in endpoint_templates:
        if "{page_no}" not in endpoint_template:
            endpoint = endpoint_template.format(project_code=PROJECT_CODE)
            success, data, msg = ds_api_get(endpoint)
            if not success:
                last_msg = msg
                continue

            if isinstance(data, list) and data:
                return True, {'totalList': data}, ''
            if isinstance(data, dict):
                total_list = data.get('totalList', [])
                if total_list:
                    return True, {'totalList': total_list}, ''
            continue

        page_no = 1
        total_pages = 1
        merged_total_list = []

        while page_no <= total_pages:
            endpoint = endpoint_template.format(project_code=PROJECT_CODE, page_no=page_no)
            success, data, msg = ds_api_get(endpoint)
            if not success:
                last_msg = msg
                merged_total_list = []
                break

            if isinstance(data, list):
                merged_total_list.extend(data)
                break

            merged_total_list.extend(data.get('totalList', []))
            total_pages = data.get('totalPage') or 1
            page_no += 1

        if merged_total_list:
            return True, {'totalList': merged_total_list}, ''

    return False, {}, last_msg


def get_schedule_map():
    """获取当前项目的调度配置映射，用于识别带定时的父工作流"""
    endpoint_templates = [
        "/projects/{project_code}/schedules?pageNo={page_no}&pageSize=200",
    ]
    schedule_map = {}

    for endpoint_template in endpoint_templates:
        page_no = 1
        total_pages = 1

        while page_no <= total_pages:
            endpoint = endpoint_template.format(project_code=PROJECT_CODE, page_no=page_no)
            success, data, msg = ds_api_get(endpoint)
            if not success:
                break

            total_list = data.get('totalList', [])
            for item in total_list:
                process_code = (
                    item.get('processDefinitionCode')
                    or item.get('workflowDefinitionCode')
                    or item.get('definitionCode')
                )
                if process_code is None:
                    continue
                schedule_map[str(process_code)] = item

            total_pages = data.get('totalPage') or 1
            page_no += 1

    return schedule_map


def get_instance_detail(project_code, instance_id):
    """兼容 DS 3.3 workflow-instances 与 DS 3.2 process-instances 详情接口"""
    endpoints = _get_instance_detail_endpoints(project_code, instance_id)
    last_msg = ""
    for endpoint in endpoints:
        success, data, msg = ds_api_get(endpoint)
        if success:
            return True, data, msg
        last_msg = msg
    return False, {}, last_msg


def _build_instance_list_endpoints(project_code, state_type=None, page_no=1, page_size=100):
    suffix = f"?pageNo={page_no}&pageSize={page_size}"
    if state_type:
        suffix += f"&stateType={state_type}"
    return [
        f"/projects/{project_code}/{style}{suffix}"
        for style in _get_instance_list_styles()
    ]


def get_instance_from_list(project_code, instance_id):
    """详情接口失败时，回退到实例列表中按 ID 查状态，避免误判已启动实例"""
    instance_id_str = str(instance_id)
    for state_type in _get_instance_state_types_for_search(include_all=False):
        items = get_all_instances_from_lists(project_code, state_type=state_type)
        debug_log(
            f"列表回查实例ID {instance_id} 使用 stateType={state_type or 'NONE'}，可见实例 {len(items)} 个"
        )
        for item in items:
            if str(item.get('id')) == instance_id_str:
                return item
    return {}


def parse_ds_datetime(value):
    """兼容解析 DS 返回的常见时间格式"""
    if not value:
        return None

    if hasattr(value, 'strftime'):
        return value

    text = str(value).strip()
    patterns = (
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%dT%H:%M:%S%z',
    )
    for pattern in patterns:
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def get_all_instances_from_lists(project_code, state_type='ALL'):
    """从实例列表接口聚合所有可见实例，用于兼容不同 DS 版本/返回口径"""
    merged_items = []
    seen_keys = set()
    last_errors = []

    for endpoint_template in _get_instance_list_styles():
        page_no = 1
        total_pages = 1

        while page_no <= total_pages:
            success = False
            data = {}
            msg = ''
            attempted_endpoints = _build_instance_list_endpoints(
                project_code=project_code,
                state_type=state_type,
                page_no=page_no,
                page_size=100,
            )
            # 针对部分版本不接受 stateType 的情况，再尝试一轮无 stateType 的列表接口。
            if state_type is not None:
                attempted_endpoints.extend(
                    _build_instance_list_endpoints(
                        project_code=project_code,
                        state_type=None,
                        page_no=page_no,
                        page_size=100,
                    )
                )

            deduped_endpoints = []
            seen_endpoint = set()
            for endpoint in attempted_endpoints:
                if endpoint_template not in endpoint or endpoint in seen_endpoint:
                    continue
                seen_endpoint.add(endpoint)
                deduped_endpoints.append(endpoint)

            for endpoint in deduped_endpoints:
                success, data, msg = ds_api_get(endpoint)
                debug_log(
                    f"实例列表查询 endpoint={endpoint} success={success} "
                    f"msg={msg or '-'} count={len(data.get('totalList', [])) if data else 0}"
                )
                if success:
                    break

            if not success:
                last_errors.append(msg)
                break

            for item in data.get('totalList', []):
                key = str(item.get('id'))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged_items.append(item)

            total_pages = data.get('totalPage') or 1
            page_no += 1

    if not merged_items and last_errors:
        debug_log(f"实例列表查询无结果，最近错误: {' | '.join([err for err in last_errors if err])}")

    return merged_items


def find_recent_instance_by_workflow(project_code, workflow_code, launched_at=None, state_types=None):
    """当 instance_id 无法直接查询时，按工作流和启动时间窗口反查最近实例"""
    if state_types is None:
        state_types = _get_instance_state_types_for_search(include_all=False)

    workflow_code_str = str(workflow_code)
    launched_at_dt = parse_ds_datetime(launched_at)
    prelaunch_skew_seconds = 15
    candidates = []
    non_scheduler_fallback_candidates = []

    for state_type in state_types:
        for item in get_all_instances_from_lists(project_code, state_type=state_type):
            item_workflow_code = (
                item.get('processDefinitionCode')
                or item.get('workflowDefinitionCode')
                or item.get('definitionCode')
            )
            if str(item_workflow_code) != workflow_code_str:
                continue

            command_type = str(item.get('commandType') or '').upper()
            if command_type != 'SCHEDULER':
                non_scheduler_fallback_candidates.append(item)

            start_dt = parse_ds_datetime(item.get('startTime'))
            if launched_at_dt and start_dt:
                if start_dt < launched_at_dt:
                    if (launched_at_dt - start_dt).total_seconds() <= prelaunch_skew_seconds:
                        candidates.append(item)
                        continue
                    continue
                diff_seconds = abs((start_dt - launched_at_dt).total_seconds())
                if diff_seconds > 600:
                    continue
            candidates.append(item)

    if not candidates:
        if non_scheduler_fallback_candidates:
            non_scheduler_fallback_candidates.sort(
                key=lambda item: (
                    parse_ds_datetime(item.get('startTime')).timestamp()
                    if parse_ds_datetime(item.get('startTime'))
                    else float('-inf')
                ),
                reverse=True,
            )
            selected = non_scheduler_fallback_candidates[0]
            debug_log(
                f"工作流 {workflow_code} 未按启动时间匹配到实例，回退选中最近非调度实例 "
                f"id={selected.get('id')} state={selected.get('state')} startTime={selected.get('startTime')}"
            )
            return selected
        debug_log(
            f"未找到工作流 {workflow_code} 的近期实例，launched_at={launched_at}, "
            f"state_types={state_types}"
        )
        return {}

    def candidate_sort_key(item):
        start_dt = parse_ds_datetime(item.get('startTime'))
        command_type = str(item.get('commandType') or '').upper()
        state = str(item.get('state') or '').upper()
        is_non_scheduler = 0 if command_type == 'SCHEDULER' else 1
        state_priority = {
            'RUNNING_EXECUTION': 4,
            'SUCCESS': 4,
            'FINISHED': 4,
            'READY_STOP': 2,
            'FAILURE': 1,
            'FAILED': 1,
            'KILL': 1,
            'STOP': 0,
        }.get(state, 3)
        if launched_at_dt and start_dt:
            distance = -abs((start_dt - launched_at_dt).total_seconds())
        else:
            distance = float('-inf')
        timestamp = start_dt.timestamp() if start_dt else float('-inf')
        return (is_non_scheduler, state_priority, distance, timestamp)

    candidates.sort(key=candidate_sort_key, reverse=True)
    debug_log(
        f"工作流 {workflow_code} 匹配到 {len(candidates)} 个近期实例，选中实例ID="
        f"{candidates[0].get('id')} state={candidates[0].get('state')}"
    )
    return candidates[0]


def maybe_replace_with_recent_real_instance(project_code, item, current_instance):
    """当启动回执实例状态不可信时，优先切换到同工作流的真实实例。"""
    task_context = item.get('task') or item
    workflow_code = item.get('workflow_code') or task_context.get('workflow_code')
    if not workflow_code:
        return current_instance

    current_id = str((current_instance or {}).get('id') or item.get('instance_id') or '')
    start_response_id = str(item.get('start_response_id') or '')
    current_state = str((current_instance or {}).get('state') or '').upper()
    should_recheck_recent = (
        not item.get('resolved_instance_id')
        or (start_response_id and current_id == start_response_id)
        or current_state in {'STOP', 'FAILED', 'FAILURE', 'KILL', 'READY_STOP'}
    )
    if not should_recheck_recent:
        return current_instance

    recent_instance = find_recent_instance_by_workflow(
        project_code,
        workflow_code,
        launched_at=task_context.get('launched_at'),
        state_types=('RUNNING_EXECUTION', 'SUCCESS', 'FAILURE', 'READY_STOP', 'FINISHED', None),
    )
    recent_id = str(recent_instance.get('id') or '')
    if not recent_id or recent_id == current_id:
        return current_instance

    item['resolved_instance_id'] = recent_instance.get('id')
    item['instance_id'] = recent_instance.get('id')
    if item.get('task'):
        item['task']['instance_id'] = recent_instance.get('id')
    else:
        item['id'] = recent_instance.get('id')
    debug_log(
        f"切换到近期真实实例 table={item.get('table') or item.get('name')} "
        f"start_response_id={item.get('start_response_id')} recent_instance_id={recent_instance.get('id')} "
        f"recent_state={recent_instance.get('state')}"
    )
    return recent_instance


def should_delay_failed_state_confirmation(item, state):
    """短暂延迟 STOP/FAILED 的最终判定，给 DS 3.3 集群一个再次识别真实实例的窗口。"""
    normalized_state = str(state or '').upper()
    if normalized_state not in {'FAILED', 'FAILURE', 'KILL', 'STOP', 'READY_STOP'}:
        return False

    workflow_code = item.get('workflow_code') or (item.get('task') or {}).get('workflow_code')
    if not workflow_code:
        return False

    first_seen_at = item.get('first_seen_at')
    if not first_seen_at:
        return False

    instance_age = time.time() - first_seen_at
    if instance_age >= FAILED_STATE_CONFIRMATION_GRACE_SECONDS:
        return False

    rechecks = int(item.get('failed_state_rechecks', 0))
    if rechecks >= 2:
        return False

    item['failed_state_rechecks'] = rechecks + 1
    return True


def collect_instance_query_diagnostics(project_code, instance_id, workflow_code=None, launched_at=None):
    """收集当前实例状态查询诊断信息，便于线上排查不同 DS 版本差异。"""
    detail_results = []
    for endpoint in _get_instance_detail_endpoints(project_code, instance_id):
        success, data, msg = ds_api_get(endpoint)
        detail_results.append(
            {
                'endpoint': endpoint,
                'success': success,
                'msg': msg,
                'state': data.get('state') if data else '',
                'id': data.get('id') if data else '',
            }
        )

    list_results = []
    for state_type in _get_instance_state_types_for_search(include_all=True):
        items = get_all_instances_from_lists(project_code, state_type=state_type)
        matched = next((item for item in items if str(item.get('id')) == str(instance_id)), None)
        list_results.append(
            {
                'state_type': state_type or 'NONE',
                'visible_count': len(items),
                'matched_id': matched.get('id') if matched else '',
                'matched_state': matched.get('state') if matched else '',
            }
        )

    recent_instance = {}
    if workflow_code:
        recent_instance = find_recent_instance_by_workflow(
            project_code,
            workflow_code,
            launched_at=launched_at,
            state_types=('RUNNING_EXECUTION', 'READY_STOP', 'FAILURE', 'SUCCESS', 'FINISHED', 'ALL', None),
        )

    return {
        'instance_id': instance_id,
        'workflow_code': workflow_code,
        'launched_at': launched_at,
        'detail_results': detail_results,
        'list_results': list_results,
        'recent_instance': {
            'id': recent_instance.get('id', ''),
            'state': recent_instance.get('state', ''),
            'startTime': recent_instance.get('startTime', ''),
        } if recent_instance else {},
    }


def get_running_instances_by_workflow(project_code, workflow_code):
    """查询指定工作流当前是否已有运行中的实例，用于避开调度执行窗口"""
    endpoints = _build_instance_list_endpoints(
        project_code=project_code,
        state_type='RUNNING_EXECUTION',
        page_no=1,
        page_size=100,
    )
    workflow_code_str = str(workflow_code)

    for endpoint in endpoints:
        success, data, msg = ds_api_get(endpoint)
        if not success:
            continue
        items = data.get('totalList', [])
        matches = []
        for item in items:
            item_workflow_code = (
                item.get('processDefinitionCode')
                or item.get('workflowDefinitionCode')
                or item.get('definitionCode')
            )
            if str(item_workflow_code) == workflow_code_str:
                matches.append(item)
        if matches:
            return matches
    return []


def find_conflicting_running_instance(project_code, workflow_code):
    """返回会与手动重跑冲突的运行中实例"""
    running_instances = get_running_instances_by_workflow(project_code, workflow_code)
    if not running_instances:
        return None

    for item in running_instances:
        command_type = str(item.get('commandType') or '').upper()
        if command_type == 'SCHEDULER':
            return item
    return running_instances[0]


def build_conflicting_instance_error(conflict_instance):
    """为运行冲突场景生成更清晰的人工处理说明"""
    instance_id = conflict_instance.get('id', '未知')
    command_type = conflict_instance.get('commandType') or 'UNKNOWN'
    state = conflict_instance.get('state') or 'UNKNOWN'
    return (
        f"目标工作流已有运行中实例，跳过本次重跑以避开调度冲突 "
        f"(实例ID: {instance_id}, 启动类型: {command_type}, 状态: {state})"
    )


def wait_for_workflow_conflict_clear(project_code, workflow_code, poll_interval=None, max_wait=None):
    """等待同一工作流的运行中实例结束，避免并发启动同一工作流内的多个单点任务。"""
    if poll_interval is None:
        poll_interval = REPAIR_WORKFLOW_CONFLICT_POLL_INTERVAL_SECONDS
    if max_wait is None:
        max_wait = REPAIR_WORKFLOW_CONFLICT_WAIT_SECONDS

    start_time = time.time()
    last_conflict = None

    while True:
        conflict_instance = find_conflicting_running_instance(project_code, workflow_code)
        if not conflict_instance:
            return True, None

        last_conflict = conflict_instance
        elapsed = int(time.time() - start_time)
        if elapsed >= max_wait:
            return False, last_conflict

        instance_id = conflict_instance.get('id', '未知')
        command_type = conflict_instance.get('commandType') or 'UNKNOWN'
        state = conflict_instance.get('state') or 'UNKNOWN'
        log(
            f"  ⏳ 同工作流已有运行实例，等待结束后再启动 "
            f"(实例ID: {instance_id}, 启动类型: {command_type}, 状态: {state}, 已等待 {elapsed}s)"
        )
        time.sleep(poll_interval)


def is_workflow_scheduled(workflow_code, schedule_map):
    """判断工作流是否挂了定时调度"""
    return str(workflow_code) in schedule_map


def build_scheduled_parent_only_error(location):
    workflow_name = location.get('workflow_name') or '未知工作流'
    return f"仅匹配到带定时的父工作流，自动修复禁止直接启动该工作流，需改为命中无定时子工作流 ({workflow_name})"


def build_blocked_workflow_error(location):
    workflow_name = location.get('workflow_name') or '未知工作流'
    return f"命中禁止自动修复的工作流，已转人工处理 ({workflow_name})"


def is_blocked_workflow_match(location):
    workflow_name = str(location.get('workflow_name') or '').strip()
    return bool(workflow_name) and workflow_name in BLOCKED_WORKFLOW_NAMES


def get_fuyan_name(workflow):
    return workflow.get('name') or workflow.get('workflow_name') or '未命名复验工作流'


def get_fuyan_code(workflow):
    return workflow.get('code') or workflow.get('workflow_code') or ''


def get_fuyan_project_code(workflow):
    return workflow.get('project_code') or workflow.get('fuyan_project_code') or FUYAN_PROJECT_CODE


def is_blocked_fuyan_workflow(workflow):
    workflow_name = get_fuyan_name(workflow).strip()
    return bool(workflow_name) and workflow_name in BLOCKED_FUYAN_WORKFLOW_NAMES


def normalize_fuyan_level(workflow):
    level = (workflow.get('level') or '').strip().lower()
    if level in {'all', '全级别'}:
        return 'all'
    if '1' in level:
        return '1'
    if '2' in level:
        return '2'
    if '3' in level:
        return '3'
    return level


def normalize_alert_monitor_level(alert):
    """从质量告警记录中提取 1/2/3 级复验口径。"""
    for key in ('monitor_level', 'alert_level', 'type', 'alert_type', 'level'):
        raw_value = alert.get(key)
        if raw_value in (None, ''):
            continue
        value = str(raw_value).strip().lower()
        if value.startswith('p') and value[1:] in {'1', '2', '3'}:
            return value[1:]
        if value in {'1', '2', '3'}:
            return value
        if '1' in value:
            return '1'
        if '2' in value:
            return '2'
        if '3' in value:
            return '3'
    return ''


def get_fuyan_start_node(workflow):
    start_node = workflow.get('start_node') or workflow.get('startNodeList') or ''
    if start_node:
        return str(start_node).strip()

    if normalize_fuyan_level(workflow) == '1':
        return '复验1级表'
    return ''


def resolve_fuyan_start_node_code(workflow):
    start_node_name = get_fuyan_start_node(workflow)
    if not start_node_name:
        return ''

    success, detail, _ = get_workflow_definition_detail(
        get_fuyan_code(workflow),
        get_fuyan_project_code(workflow),
    )
    if not success:
        return start_node_name

    for task in detail.get('taskDefinitionList', []):
        if str(task.get('name') or '').strip() == start_node_name:
            task_code = task.get('code')
            if task_code not in (None, ''):
                return str(task_code)

    return start_node_name


def select_fuyan_workflows(alerts):
    """智能选择复验工作流：优先按告警级别精确选择，缺失级别时保留历史兜底策略。"""
    selected_levels = set()
    has_dwb_alert = False
    has_explicit_level = False
    needs_week_recheck = False
    for alert in alerts or []:
        table = (alert.get('table') or '').lower()
        is_dws_alert = table.startswith('dws_')

        alert_level = normalize_alert_monitor_level(alert)
        if alert_level in {'1', '2', '3'}:
            has_explicit_level = True
            selected_levels.add(alert_level)
            if is_dws_alert:
                selected_levels.add('3')
                needs_week_recheck = True
            continue

        if is_dws_alert:
            needs_week_recheck = True
            selected_levels.add('3')

        if table.startswith('dwb_'):
            has_dwb_alert = True
            selected_levels.add('1')
        else:
            selected_levels.add('1')
            selected_levels.add('3')

    selected = []
    seen_codes = set()
    for workflow in FUYAN_WORKFLOWS:
        if is_blocked_fuyan_workflow(workflow):
            continue
        workflow_code = get_fuyan_code(workflow)
        workflow_level = normalize_fuyan_level(workflow)
        workflow_name = get_fuyan_name(workflow)
        include = False
        if workflow_level == 'all':
            include = (
                (not has_explicit_level and not has_dwb_alert)
                or needs_week_recheck
            ) and workflow_name.startswith('每日复验全级别数据')
        elif workflow_level in selected_levels:
            include = True

        if include and workflow_code not in seen_codes:
            selected.append(workflow)
            seen_codes.add(workflow_code)
    return selected


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def ds_api_get(endpoint):
    """DS API GET请求"""
    url = f"{DS_BASE}{endpoint}"
    req = urllib.request.Request(url)
    req.add_header('token', DS_TOKEN)
    req.add_header('Accept', 'application/json, text/plain, */*')
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result.get('code') == 0, result.get('data', {}), result.get('msg', '')
    except Exception as e:
        return False, {}, str(e)


def ds_api_post(endpoint, data):
    """DS API POST请求"""
    url = f"{DS_BASE}{endpoint}"
    encoded_data = urlencode(data).encode('utf-8')
    req = urllib.request.Request(
        url, data=encoded_data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST'
    )
    req.add_header('token', DS_TOKEN)
    req.add_header('Accept', 'application/json, text/plain, */*')
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result.get('code') == 0, result, result.get('msg', '')
    except Exception as e:
        return False, {}, str(e)


def normalize_to_datetime(value):
    """将数据库中的时间字段尽量标准化为 datetime"""
    if not value:
        return None

    if hasattr(value, 'strftime'):
        return value

    text = str(value).strip()
    for pattern in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S.%fZ'):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def resolve_alert_dt(row, now=None):
    """解析告警对应的修复 dt，优先 begin，其次 end-1 天，最后兜底当天"""
    if now is None:
        now = datetime.now()

    begin_time = normalize_to_datetime(row.get('begin'))
    if begin_time:
        return begin_time.strftime('%Y-%m-%d')

    end_time = normalize_to_datetime(row.get('end'))
    if end_time:
        return (end_time - timedelta(days=1)).strftime('%Y-%m-%d')

    return now.strftime('%Y-%m-%d')


def get_alert_window_status(row, now=None, lookback_days=None):
    """根据告警窗口跨度判断是否超出自动修复范围。"""
    if now is None:
        now = datetime.now()
    if lookback_days is None:
        lookback_days = SCAN_LOOKBACK_DAYS

    lookback_start = now.date() - timedelta(days=lookback_days)
    repair_dt_text = resolve_alert_dt(row, now=now)
    repair_dt = None
    try:
        repair_dt = datetime.strptime(repair_dt_text, '%Y-%m-%d').date() if repair_dt_text else None
    except ValueError:
        repair_dt = None

    begin_time = normalize_to_datetime(row.get('begin'))
    end_time = normalize_to_datetime(row.get('end'))
    begin_date = begin_time.date() if begin_time else None
    end_date = end_time.date() if end_time else None
    window_span_days = None
    if begin_time and end_time:
        window_span_days = (end_time - begin_time).total_seconds() / 86400
    latest_alert_date = None
    if end_date:
        latest_alert_date = end_date - timedelta(days=1)
    elif begin_date:
        latest_alert_date = begin_date

    status = {
        'is_out_of_window': False,
        'reason': '',
        'begin_date': begin_date.isoformat() if begin_date else None,
        'end_date': end_date.isoformat() if end_date else None,
        'repair_dt': repair_dt.isoformat() if repair_dt else repair_dt_text,
        'latest_alert_dt': latest_alert_date.isoformat() if latest_alert_date else None,
        'lookback_start': lookback_start.isoformat(),
        'window_span_days': window_span_days,
    }

    if window_span_days is not None and window_span_days > lookback_days:
        status['is_out_of_window'] = True
        status['reason'] = 'window_span_exceeds_limit'
        return status

    if latest_alert_date and latest_alert_date < lookback_start:
        status['is_out_of_window'] = True
        status['reason'] = 'latest_alert_dt_before_lookback'
        return status

    return status


def is_alert_out_of_window(alert_dt, now=None, lookback_days=None):
    """按告警对应 dt 判断是否超过自动修复时间窗口。"""
    if now is None:
        now = datetime.now()
    if lookback_days is None:
        lookback_days = SCAN_LOOKBACK_DAYS
    if not alert_dt:
        return False

    try:
        dt_value = datetime.strptime(str(alert_dt), '%Y-%m-%d')
    except ValueError:
        return False

    return (now.date() - dt_value.date()).days > lookback_days


def resolve_repair_table(row):
    """统一决定当前告警展示哪张表，优先使用目标表名。"""
    src_tbl = row.get('src_tbl') or ''
    dest_tbl = row.get('dest_tbl') or ''
    return dest_tbl or src_tbl

def build_search_tables(row):
    """构造查找修复任务时使用的候选表名。

    质量校验里 dest_tbl 是待修复目标表；src_tbl 只是对比表。
    只有 dest_tbl 缺失时，才用 src_tbl 兜底，避免误启动上游/对比表工作流。
    """
    search_tables = []
    for table_name in (resolve_repair_table(row),):
        normalized = str(table_name).strip()
        if normalized and normalized not in search_tables:
            search_tables.append(normalized)
    return search_tables


def count_remaining_alert_tables():
    """统计当前剩余未处理告警的去重表数，口径与扫描阶段保持一致"""
    return len(get_remaining_alert_tables())


def get_remaining_alert_tables(now=None):
    """查询当前数据库中仍未处理的去重告警表集合"""
    if now is None:
        now = datetime.now()

    from alert.db_config import get_db_connection

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT src_db, src_tbl, dest_db, dest_tbl, `begin`, `end`
                FROM {quality_result_table}
                WHERE result = 1 AND is_repaired = 0
                ORDER BY created_at DESC
            """.format(
                quality_result_table=QUALITY_RESULT_TABLE,
            )
            cursor.execute(sql)
            rows = cursor.fetchall()
    finally:
        conn.close()

    unique_tables = set()
    for row in rows:
        window_status = get_alert_window_status(row, now=now)
        if window_status['is_out_of_window']:
            continue
        table_name = resolve_repair_table(row)
        if table_name:
            unique_tables.add(table_name)

    return unique_tables


def step1_scan_alerts(now=None):
    """步骤1: 扫描告警"""
    if now is None:
        now = datetime.now()

    log("="*70)
    log("【步骤1】扫描告警")
    log("="*70)
    
    alerts = []
    try:
        from alert.db_config import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cursor:
            sql = """
                SELECT id, name, src_db, src_tbl, dest_db, dest_tbl, `begin`, `end`, diff
                FROM {quality_result_table}
                WHERE result = 1 AND is_repaired = 0
                ORDER BY created_at DESC
            """.format(
                quality_result_table=QUALITY_RESULT_TABLE,
            )
            cursor.execute(sql)
            rows = cursor.fetchall()
            
            for row in rows:
                table_name = resolve_repair_table(row)
                
                dt = resolve_alert_dt(row, now=now)
                window_status = get_alert_window_status(row, now=now)
                alert = {
                    'id': row['id'],
                    'table': table_name,
                    'src_tbl': row.get('src_tbl', ''),
                    'dest_tbl': row.get('dest_tbl', ''),
                    'search_tables': build_search_tables(row),
                    'dt': dt,
                    'name': row.get('name', ''),
                    'diff': row.get('diff', '')
                }
                if window_status['is_out_of_window']:
                    begin_text = window_status.get('begin_date') or '未知'
                    end_text = window_status.get('end_date') or '未知'
                    alert['status'] = 'skipped_out_of_window'
                    if window_status['reason'] == 'window_span_exceeds_limit':
                        span_days = window_status.get('window_span_days')
                        span_text = (
                            f"{span_days:.1f}".rstrip('0').rstrip('.')
                            if isinstance(span_days, (int, float))
                            else '未知'
                        )
                        alert['error'] = (
                            f"告警窗口 begin={begin_text}, end={end_text}，"
                            f"跨度 {span_text} 天超过自动修复阈值 {SCAN_LOOKBACK_DAYS} 天，转人工处理"
                        )
                    elif window_status['reason'] == 'latest_alert_dt_before_lookback':
                        alert['error'] = (
                            f"告警窗口 begin={begin_text}, end={end_text}，"
                            f"最新告警日期 dt={window_status.get('latest_alert_dt') or window_status.get('repair_dt') or '未知'} 早于自动修复窗口起点 "
                            f"{window_status['lookback_start']}，转人工处理"
                        )
                alerts.append(alert)
        
        conn.close()
        log(f"✅ 查询到 {len(alerts)} 条异常记录")
        
    except Exception as e:
        log(f"❌ 查询数据库失败: {e}")
        return []
    
    # 去重
    table_alerts = {}
    for alert in alerts:
        table = alert['table']
        if table and table not in table_alerts:
            table_alerts[table] = alert
    
    unique_alerts = list(table_alerts.values())
    log(f"📊 去重后: {len(unique_alerts)} 个")
    for alert in unique_alerts[:5]:  # 只显示前5个
        log(f"  ✅ {alert['table']} (dt={alert['dt']})")
    if len(unique_alerts) > 5:
        log(f"  ... 还有 {len(unique_alerts)-5} 个")

    out_of_window_count = sum(1 for alert in unique_alerts if alert.get('status') == 'skipped_out_of_window')
    if out_of_window_count:
        log(f"  ⚠️ 告警窗口跨度超过{SCAN_LOOKBACK_DAYS}天: {out_of_window_count} 个")
    
    return unique_alerts


def normalize_task_params(task):
    """将 taskParams 统一解析为 dict。"""
    task_params = task.get('taskParams', '{}')
    if isinstance(task_params, str):
        try:
            task_params = json.loads(task_params)
        except Exception:
            task_params = {}
    return task_params if isinstance(task_params, dict) else {}


def extract_subprocess_workflow_code(task, task_params=None):
    """从子流程节点及其参数里尽量提取子工作流 code。"""
    if task_params is None:
        task_params = normalize_task_params(task)

    def find_code(value):
        if isinstance(value, dict):
            for key in (
                'processDefinitionCode',
                'workflowDefinitionCode',
                'definitionCode',
                'processCode',
                'workflowCode',
                'subProcessCode',
                'subWorkflowCode',
            ):
                candidate = value.get(key)
                if candidate not in (None, ''):
                    return str(candidate)
            for nested_value in value.values():
                candidate = find_code(nested_value)
                if candidate:
                    return candidate
            return ''
        if isinstance(value, list):
            for item in value:
                candidate = find_code(item)
                if candidate:
                    return candidate
        return ''

    direct_code = find_code(task)
    if direct_code:
        return direct_code
    return find_code(task_params)


def is_subprocess_task(task_type):
    normalized = str(task_type or '').strip().upper()
    return normalized in {'SUB_PROCESS', 'SUB_PROCESS_NODE', 'SUBPROCESS'}


def should_block_scheduled_workflow_match(location):
    """仅当命中的是父工作流里的子流程节点时，才禁止直接启动。"""
    if not location:
        return False
    return is_subprocess_task(location.get('task_type'))


def step2_search_in_workflow(workflow_code, table_name, visited=None):
    """在指定工作流中搜索表"""
    workflow_code = str(workflow_code)
    visited = set(visited or set())
    if workflow_code in visited:
        return None
    visited.add(workflow_code)

    success, detail, msg = get_workflow_definition_detail(workflow_code)
    if not success:
        return None
    
    search_term = strip_table_prefix(table_name)
    tasks = detail.get('taskDefinitionList', [])
    workflow_name = get_workflow_name_from_detail(detail)
    candidates = []
    child_candidates = []

    def build_candidate(task, task_name):
        return {
            'workflow_code': workflow_code,
            'workflow_name': workflow_name,
            'task_code': task.get('code'),
            'task_name': task_name,
            'task_flag': task.get('flag', 'YES'),
            'task_type': (task.get('taskType') or '').upper(),
        }
    
    for task in tasks:
        task_name = task.get('name', '')
        task_name_lower = task_name.lower()
        
        # 排除维护任务
        is_maintenance = any(kw in task_name_lower for kw in MAINTENANCE_KEYWORDS)
        if is_maintenance:
            continue
        
        # 匹配任务名
        task_params = normalize_task_params(task)

        if is_task_name_match(task_name, table_name):
            candidate = build_candidate(task, task_name)
            if is_subprocess_task(candidate.get('task_type')):
                child_workflow_code = extract_subprocess_workflow_code(task, task_params)
                if child_workflow_code and child_workflow_code != workflow_code:
                    child_result = step2_search_in_workflow(child_workflow_code, table_name, visited=visited)
                    if child_result:
                        child_candidates.append(child_result)
                        continue
            candidates.append(candidate)
            continue
        
        # 匹配SQL
        sql = task_params.get('sql', '')
        if sql_targets_table(sql, table_name):
            candidates.append(build_candidate(task, task_name))
    
    if child_candidates:
        runnable_child_candidates = [
            candidate for candidate in child_candidates
            if str(candidate.get('task_flag', 'YES')).upper() != 'NO'
        ]
        if runnable_child_candidates:
            non_datax_runnable_child_candidates = [
                candidate for candidate in runnable_child_candidates
                if candidate.get('task_type') != 'DATAX'
            ]
            if non_datax_runnable_child_candidates:
                return non_datax_runnable_child_candidates[0]
            return runnable_child_candidates[0]
        non_datax_child_candidates = [
            candidate for candidate in child_candidates
            if candidate.get('task_type') != 'DATAX'
        ]
        if non_datax_child_candidates:
            return non_datax_child_candidates[0]
        return child_candidates[0]

    if not candidates:
        return None

    runnable_candidates = [
        candidate for candidate in candidates
        if str(candidate.get('task_flag', 'YES')).upper() != 'NO'
    ]
    if runnable_candidates:
        non_datax_runnable_candidates = [
            candidate for candidate in runnable_candidates
            if candidate.get('task_type') != 'DATAX'
        ]
        if non_datax_runnable_candidates:
            return non_datax_runnable_candidates[0]
        return runnable_candidates[0]

    non_datax_candidates = [
        candidate for candidate in candidates
        if candidate.get('task_type') != 'DATAX'
    ]
    if non_datax_candidates:
        return non_datax_candidates[0]
    
    return candidates[0]


def step2_find_locations(alerts):
    """步骤2: 查找工作流位置 - 优化版（缓存工作流列表）"""
    log("\n" + "="*70)
    log("【步骤2】查找工作流位置")
    log("="*70)
    
    # 优先搜索这些工作流（提高效率）
    priority_workflows = PRIORITY_WORKFLOWS
    
    # 缓存所有工作流列表（只获取一次）
    all_workflows = None
    schedule_map = None
    
    tasks = []
    found_count = 0
    
    for alert in alerts:
        table = alert['table']
        log(f"🔍 {table}")
        search_tables = alert.get('search_tables') or [table]

        if alert.get('status') == 'skipped_out_of_window':
            task = {
                'alert_id': alert['id'],
                'table': table,
                'src_tbl': alert.get('src_tbl', ''),
                'dest_tbl': alert.get('dest_tbl', ''),
                'search_tables': search_tables,
                'dt': alert['dt'],
                'diff': alert.get('diff'),
                'workflow_code': '',
                'workflow_name': '超出自动修复窗口',
                'task_code': '',
                'task_name': '',
                'task_flag': '',
                'status': 'skipped_out_of_window',
                'error': alert.get('error', ''),
            }
            log(f"  ⏭️ {task['error']}")
            tasks.append(task)
            continue
        
        location = None
        scheduled_location = None
        blocked_location = None
        forbidden_location = None
        # 先在优先工作流中搜索
        for wf_code, wf_name in priority_workflows:
            for search_table in search_tables:
                result = step2_search_in_workflow(wf_code, search_table)
                if not result:
                    continue
                if str(result.get('task_flag', 'YES')).upper() == 'NO':
                    if forbidden_location is None:
                        forbidden_location = result
                    continue
                if is_blocked_workflow_match(result):
                    if blocked_location is None:
                        blocked_location = result
                    continue
                if schedule_map is None:
                    schedule_map = get_schedule_map()
                if (
                    is_workflow_scheduled(result['workflow_code'], schedule_map)
                    and should_block_scheduled_workflow_match(result)
                ):
                    scheduled_location = result
                    continue
                location = result
                break
            if location:
                break
        
        # 如果没找到，再搜索所有工作流（使用缓存）
        if not location:
            if all_workflows is None:
                log(f"  在优先工作流中未找到，获取所有工作流列表...")
                success, data, msg = get_workflow_definition_list()
                if success:
                    all_workflows = data.get('totalList', [])
                    log(f"  获取到 {len(all_workflows)} 个工作流")
                else:
                    log(f"  ❌ 获取工作流列表失败: {msg}")
                    all_workflows = []
            
            # 在缓存的工作流中搜索
            for wf in all_workflows:
                wf_code = (
                    wf.get('code')
                    or wf.get('workflowDefinitionCode')
                    or wf.get('processDefinitionCode')
                    or wf.get('definitionCode')
                )
                # 跳过已在priority中搜索过的工作流
                if wf_code not in [pw[0] for pw in priority_workflows]:
                    for search_table in search_tables:
                        result = step2_search_in_workflow(wf_code, search_table)
                        if not result:
                            continue
                        if str(result.get('task_flag', 'YES')).upper() == 'NO':
                            if forbidden_location is None:
                                forbidden_location = result
                            continue
                        if is_blocked_workflow_match(result):
                            if blocked_location is None:
                                blocked_location = result
                            continue
                        if schedule_map is None:
                            schedule_map = get_schedule_map()
                        if (
                            is_workflow_scheduled(result['workflow_code'], schedule_map)
                            and should_block_scheduled_workflow_match(result)
                        ):
                            if scheduled_location is None:
                                scheduled_location = result
                            continue
                        location = result
                        break
                    if location:
                        break
        
        if location:
            task = {
                'alert_id': alert['id'],
                'table': table,
                'src_tbl': alert.get('src_tbl', ''),
                'dest_tbl': alert.get('dest_tbl', ''),
                'search_tables': search_tables,
                'dt': alert['dt'],
                'diff': alert.get('diff'),
                'workflow_code': location['workflow_code'],
                'workflow_name': location['workflow_name'],
                'task_code': location['task_code'],
                'task_name': location['task_name'],
                'task_flag': location.get('task_flag', 'YES'),
            }
            log(f"  ✅ {location['workflow_name']} -> {location['task_name']}")
            found_count += 1
        elif scheduled_location:
            error_msg = build_scheduled_parent_only_error(scheduled_location)
            task = {
                'alert_id': alert['id'],
                'table': table,
                'src_tbl': alert.get('src_tbl', ''),
                'dest_tbl': alert.get('dest_tbl', ''),
                'search_tables': search_tables,
                'dt': alert['dt'],
                'diff': alert.get('diff'),
                'workflow_code': '',
                'workflow_name': scheduled_location['workflow_name'],
                'task_code': '',
                'task_name': scheduled_location.get('task_name', ''),
                'task_flag': scheduled_location.get('task_flag', ''),
                'error': error_msg,
            }
            log(f"  ⏭️ {error_msg}")
        elif blocked_location:
            error_msg = build_blocked_workflow_error(blocked_location)
            task = {
                'alert_id': alert['id'],
                'table': table,
                'src_tbl': alert.get('src_tbl', ''),
                'dest_tbl': alert.get('dest_tbl', ''),
                'search_tables': search_tables,
                'dt': alert['dt'],
                'diff': alert.get('diff'),
                'workflow_code': '',
                'workflow_name': blocked_location['workflow_name'],
                'task_code': '',
                'task_name': blocked_location.get('task_name', ''),
                'task_flag': blocked_location.get('task_flag', ''),
                'error': error_msg,
            }
            log(f"  ⏭️ {error_msg}")
        elif forbidden_location:
            error_msg = build_forbidden_task_manual_review_reason(forbidden_location)
            task = {
                'alert_id': alert['id'],
                'table': table,
                'src_tbl': alert.get('src_tbl', ''),
                'dest_tbl': alert.get('dest_tbl', ''),
                'search_tables': search_tables,
                'dt': alert['dt'],
                'diff': alert.get('diff'),
                'workflow_code': '',
                'workflow_name': forbidden_location['workflow_name'],
                'task_code': '',
                'task_name': forbidden_location.get('task_name', ''),
                'task_flag': forbidden_location.get('task_flag', ''),
                'error': error_msg,
            }
            log(f"  ⏭️ {error_msg}")
        else:
            task = {
                'alert_id': alert['id'],
                'table': table,
                'src_tbl': alert.get('src_tbl', ''),
                'dest_tbl': alert.get('dest_tbl', ''),
                'search_tables': search_tables,
                'dt': alert['dt'],
                'diff': alert.get('diff'),
                'workflow_code': '',
                'workflow_name': '未找到',
                'task_code': '',
                'task_name': '',
                'task_flag': '',
            }
            log(f"  ❌ 未找到")
        
        tasks.append(task)
    
    log(f"\n📊 找到 {found_count}/{len(alerts)} 个工作流")
    return tasks


def load_manual_review_state():
    """加载人工处理策略状态"""
    if not os.path.exists(MANUAL_REVIEW_STATE_FILE):
        return {}

    try:
        with open(MANUAL_REVIEW_STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ 读取人工处理状态失败: {e}")
        return {}


def save_manual_review_state(state):
    """保存人工处理策略状态"""
    os.makedirs(os.path.dirname(MANUAL_REVIEW_STATE_FILE), exist_ok=True)
    with open(MANUAL_REVIEW_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def is_suspected_redundant_data(task):
    """根据 diff 判断是否为疑似冗余数据告警"""
    diff = task.get('diff')
    if diff in (None, ''):
        return False

    try:
        return float(diff) < 0
    except (TypeError, ValueError):
        return False


def build_redundant_data_manual_review_reason():
    """构造疑似冗余/底层少数场景的人工处理提示"""
    return '疑似当前层数据多于底层，重跑一次后仍未恢复，建议检查底层是否需要删数，并人工判断修复'


def build_forbidden_task_manual_review_reason(task):
    """构造节点被禁用时的人工处理提示"""
    diff = task.get('diff')
    diff_suffix = f"，数据量差异: {diff}" if diff not in (None, '') else ''
    workflow_name = task.get('workflow_name') or '未知工作流'
    task_name = task.get('task_name') or task.get('table') or '未知节点'
    return f"节点被配置为禁止执行，需人工查看修复（工作流: {workflow_name}，节点: {task_name}{diff_suffix}）"


def apply_repair_strategy(tasks, strategy_state):
    """应用修复策略：疑似冗余数据仅允许自动重跑一次"""
    runnable_tasks = []
    manual_review_tasks = []

    for task in tasks:
        if str(task.get('task_flag', 'YES')).upper() == 'NO':
            manual_task = dict(task)
            manual_task['status'] = 'skipped_manual_review'
            manual_task['error'] = build_forbidden_task_manual_review_reason(task)
            manual_review_tasks.append(manual_task)
            continue

        if not is_suspected_redundant_data(task):
            runnable_tasks.append(task)
            continue

        table_state = strategy_state.get(task['table'], {}).get(task['dt'], {})
        if table_state.get('redundant_retry_done'):
            manual_task = dict(task)
            manual_task['status'] = 'skipped_manual_review'
            manual_task['error'] = build_redundant_data_manual_review_reason()
            manual_review_tasks.append(manual_task)
        else:
            runnable_tasks.append(task)

    return runnable_tasks, manual_review_tasks


def record_redundant_retry_attempt(strategy_state, completed_tasks):
    """记录疑似冗余数据告警的首次自动重跑尝试"""
    for task in completed_tasks:
        if not is_suspected_redundant_data(task):
            continue

        table_state = strategy_state.setdefault(task['table'], {})
        dt_state = table_state.setdefault(task['dt'], {})
        dt_state['redundant_retry_done'] = True
        dt_state.setdefault('manual_review_required', False)
        dt_state['last_completed_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def record_manual_review_tasks(strategy_state, manual_review_tasks):
    """记录需要人工处理的任务状态"""
    for task in manual_review_tasks:
        table_state = strategy_state.setdefault(task['table'], {})
        dt_state = table_state.setdefault(task['dt'], {})
        dt_state['redundant_retry_done'] = True
        dt_state['manual_review_required'] = True
        dt_state['reason'] = task.get('error', '需人工处理')
        dt_state['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def step3_start_repair(tasks):
    """步骤3: 启动修复任务"""
    log("\n" + "="*70)
    log("【步骤3】启动修复任务")
    log("="*70)
    
    results = []
    running_instances = []
    
    for i, task in enumerate(tasks, 1):
        table = task['table']
        dt = task['dt']
        workflow_code = task.get('workflow_code')
        task_code = task.get('task_code')

        if task.get('status') == 'skipped_out_of_window':
            log(f"[{i}/{len(tasks)}] ⏭️ {table} - 超出自动修复窗口，转人工处理")
            task['status'] = 'skipped_manual_review'
            task['error'] = task.get('error') or '告警超出自动修复窗口，需人工处理'
            results.append(task)
            continue
        
        if not workflow_code or not task_code:
            log(f"[{i}/{len(tasks)}] ⏭️ {table} - 未找到工作流")
            task['status'] = 'skipped_no_workflow'
            results.append(task)
            continue
        
        log(f"\n[{i}/{len(tasks)}] {table}")
        log(f"  工作流: {task['workflow_name']}")
        log(f"  任务: {task['task_name']}")

        conflict_instance = find_conflicting_running_instance(PROJECT_CODE, workflow_code)
        if conflict_instance:
            wait_success, remaining_conflict = wait_for_workflow_conflict_clear(
                PROJECT_CODE,
                workflow_code,
            )
            if not wait_success:
                error_msg = build_conflicting_instance_error(remaining_conflict or conflict_instance)
                log(f"  ⏭️ 等待超时，跳过启动: {error_msg}")
                task['status'] = 'failed'
                task['error'] = error_msg
                results.append(task)
                continue
            log("  ✅ 同工作流运行实例已结束，继续启动当前任务")
        
        base_data = {
            'startNodeList': task_code,
            'taskDependType': 'TASK_ONLY',
            'failureStrategy': 'CONTINUE',
            'warningType': 'NONE',
            'warningGroupId': 0,
            'execType': 'START_PROCESS',
            'environmentCode': DS_ENVIRONMENT_CODE,
            'tenantCode': DS_TENANT_CODE,
            'dryRun': 0,
        }
        success, result, msg, used_endpoint, used_payload, launched_at = start_workflow_instance_with_fallbacks(
            PROJECT_CODE,
            workflow_code,
            base_data,
            dt=dt,
            table=table,
        )

        if success:
            instance_id = _extract_instance_id_from_start_result(result)
            debug_log(
                f"启动成功 table={table} endpoint={used_endpoint} payload_keys={sorted(used_payload.keys())} "
                f"raw_data={result.get('data')}"
            )
            log(f"  ✅ 启动成功，实例ID: {instance_id}")
            task['status'] = 'success'
            task['start_response_id'] = instance_id
            task['instance_id'] = instance_id
            task['launched_at'] = launched_at
            running_instances.append({
                'table': table,
                'instance_id': instance_id,
                'start_response_id': instance_id,
                'resolved_instance_id': None,
                'workflow_code': workflow_code,
                'task': task
            })
        else:
            error_msg = result.get('msg', msg or '未知错误')
            log(f"  ❌ 启动失败: {error_msg}")
            task['status'] = 'failed'
            task['error'] = error_msg
        
        results.append(task)
        time.sleep(1)
    
    log(f"\n📊 启动结果: {len(running_instances)} 个成功, {len(tasks) - len(running_instances)} 个跳过/失败")
    return results, running_instances


def step4_wait_and_check(running_instances, poll_interval=10, max_wait=60):
    """步骤4: 动态监控任务状态（每30秒检查一次）- 修复版（增加失败次数限制）"""
    if not running_instances:
        log("\n  没有需要等待的任务")
        return [], []
    
    log("\n" + "="*70)
    log("【步骤4】动态监控任务状态")
    log("="*70)
    log(f"  共 {len(running_instances)} 个任务")
    log(f"  轮询间隔: {poll_interval}秒")
    log(f"  最大等待: {max_wait}秒")
    
    start_time = time.time()
    completed_tasks = []
    failed_tasks = []
    pending = running_instances.copy()
    
    # 初始化失败计数
    for item in pending:
        item['fail_count'] = 0
        item['first_seen_at'] = time.time()
        item['failed_state_rechecks'] = 0
    
    check_count = 0
    
    while pending and (time.time() - start_time) < max_wait:
        elapsed = int(time.time() - start_time)
        check_count += 1
        log(f"\n⏱️  第{check_count}次检查 (已等待 {elapsed}秒)")
        log("-" * 70)
        
        still_pending = []
        status_changed = False
        
        for item in pending:
            table = item['table']
            instance_id = item.get('resolved_instance_id') or item['instance_id']
            workflow_code = item.get('workflow_code') or item['task'].get('workflow_code')

            # 对印尼这类 process-instances 风格集群，start-process-instance 返回值可能只是启动回执，
            # 需要先从实例列表里按工作流 code + 启动时间找到真实实例，再进入详情/状态轮询。
            discovered_instance = {}
            if not item.get('resolved_instance_id') and workflow_code:
                discovered_instance = find_recent_instance_by_workflow(
                    PROJECT_CODE,
                    workflow_code,
                    launched_at=item['task'].get('launched_at'),
                )
                if discovered_instance.get('id') is not None:
                    resolved_instance_id = discovered_instance.get('id')
                    item['resolved_instance_id'] = resolved_instance_id
                    item['instance_id'] = resolved_instance_id
                    item['task']['instance_id'] = resolved_instance_id
                    debug_log(
                        f"发现真实实例 table={table} start_response_id={item.get('start_response_id')} "
                        f"resolved_instance_id={resolved_instance_id}"
                    )
                    instance_id = resolved_instance_id
            
            # 查询实例状态
            success, data, msg = get_instance_detail(PROJECT_CODE, instance_id)
            if not success or not data:
                fallback_data = get_instance_from_list(PROJECT_CODE, instance_id)
                if fallback_data:
                    success = True
                    data = fallback_data
                    msg = ''
                elif discovered_instance:
                    success = True
                    data = discovered_instance
                    msg = ''
                elif workflow_code:
                    recent_instance = find_recent_instance_by_workflow(
                        PROJECT_CODE,
                        workflow_code,
                        launched_at=item['task'].get('launched_at'),
                    )
                    if recent_instance:
                        if recent_instance.get('id') is not None:
                            item['resolved_instance_id'] = recent_instance.get('id')
                            item['instance_id'] = recent_instance.get('id')
                            item['task']['instance_id'] = recent_instance.get('id')
                        success = True
                        data = recent_instance
                        msg = ''
            
            if success and data:
                data = maybe_replace_with_recent_real_instance(PROJECT_CODE, item, data)
                state = data.get('state', 'UNKNOWN')
                if data.get('id') is not None:
                    item['instance_id'] = data.get('id')
                    item['task']['instance_id'] = data.get('id')
                
                if state in ['SUCCESS', 'FINISHED']:
                    log(f"  ✅ {table}: 完成 ({state})")
                    item['task']['final_status'] = 'success'
                    item['task']['end_time'] = data.get('endTime')
                    completed_tasks.append(item['task'])
                    status_changed = True
                elif state in ['FAILED', 'KILL', 'STOP']:
                    if should_delay_failed_state_confirmation(item, state):
                        log(f"  ⚠️  {table}: 检测到 {state}，继续等待并重查真实实例")
                        still_pending.append(item)
                        continue
                    log(f"  ❌ {table}: 失败 ({state})")
                    item['task']['final_status'] = 'failed'
                    item['task']['error'] = f"状态: {state}"
                    failed_tasks.append(item['task'])
                    status_changed = True
                else:
                    # 仍在运行中，重置失败计数
                    item['fail_count'] = 0
                    log(f"  ⏳ {table}: {state}")
                    still_pending.append(item)
            else:
                # 查询失败，增加失败计数
                item['fail_count'] += 1
                instance_age = time.time() - item.get('first_seen_at', start_time)
                # 超过 60s 还无法拿到明确状态则直接判失败，避免流程长期卡住。
                if instance_age >= max_wait:
                    diagnostics = collect_instance_query_diagnostics(
                        PROJECT_CODE,
                        instance_id=item['instance_id'],
                        workflow_code=workflow_code,
                        launched_at=item['task'].get('launched_at'),
                    )
                    debug_log(
                        f"实例查询失败 table={table} instance_id={item['instance_id']} "
                        f"diagnostics={json.dumps(diagnostics, ensure_ascii=False)}"
                    )
                    log(f"  ❌ {table}: 超过{max_wait}秒仍未获取到明确状态 ({msg})")
                    item['task']['final_status'] = 'timeout'
                    item['task']['error'] = f"超过{max_wait}秒未获取到明确状态: {msg}"
                    failed_tasks.append(item['task'])
                    status_changed = True
                else:
                    log(f"  ⚠️  {table}: 查询失败 ({msg})，第{item['fail_count']}次")
                    still_pending.append(item)
        
        pending = still_pending
        
        # 显示汇总
        log("-" * 70)
        log(f"📊 当前状态: 成功={len(completed_tasks)}, 失败={len(failed_tasks)}, 运行中={len(pending)}")
        
        # 如果都完成了，立即退出
        if not pending:
            log(f"\n🎉 所有任务已完成！")
            break
        
        # 等待下一轮
        log(f"  还有 {len(pending)} 个任务运行中，{poll_interval}秒后再次检查...")
        time.sleep(poll_interval)
    
    # 处理超时任务
    if pending:
        log(f"\n⚠️  等待超时，以下任务未完成:")
        for item in pending:
            log(f"    - {item['table']}: {item['instance_id']}")
            item['task']['final_status'] = 'timeout'
            failed_tasks.append(item['task'])
    
    log(f"\n📊 最终结果:")
    log(f"  ✅ 成功: {len(completed_tasks)} 个")
    log(f"  ❌ 失败/超时: {len(failed_tasks)} 个")
    
    return completed_tasks, failed_tasks


def get_task_execution_key(task):
    """为同一个子任务生成串行执行键，避免重复并发启动"""
    workflow_code = task.get('workflow_code') or ''
    task_code = task.get('task_code') or ''
    return f"{workflow_code}::{task_code}"


def split_ready_and_blocked_tasks(tasks, in_flight_keys):
    """把可立即启动的任务与需排队等待的同子任务任务拆开"""
    ready_tasks = []
    blocked_tasks = []
    seen_keys = set(in_flight_keys)

    for task in tasks:
        workflow_code = task.get('workflow_code')
        task_code = task.get('task_code')

        if not workflow_code or not task_code:
            ready_tasks.append(task)
            continue

        execution_key = get_task_execution_key(task)
        if execution_key in seen_keys:
            blocked_tasks.append(task)
            continue

        ready_tasks.append(task)
        seen_keys.add(execution_key)

    return ready_tasks, blocked_tasks


def execute_repairs_in_batches(tasks, max_parallel=4):
    """分批执行修复任务，控制同时运行的实例数量。"""
    if max_parallel <= 0:
        raise ValueError("max_parallel must be greater than 0")

    all_results = []
    all_completed_tasks = []
    all_failed_tasks = []

    total_batches = (len(tasks) + max_parallel - 1) // max_parallel

    for batch_index, start in enumerate(range(0, len(tasks), max_parallel), 1):
        batch_tasks = tasks[start:start + max_parallel]
        log("\n" + "=" * 70)
        log(f"【批次 {batch_index}/{total_batches}】执行 {len(batch_tasks)} 个修复任务")
        log("=" * 70)

        batch_results, running_instances = step3_start_repair(batch_tasks)
        completed_tasks, failed_tasks = step4_wait_and_check(running_instances)

        all_results.extend(batch_results)
        all_completed_tasks.extend(completed_tasks)
        all_failed_tasks.extend(failed_tasks)

    return all_results, all_completed_tasks, all_failed_tasks


def step5_execute_fuyan(completed_tasks, failed_tasks, alerts):
    """步骤5: 执行复验"""
    log("\n" + "="*70)
    log("【步骤5】执行复验")
    log("="*70)

    if not completed_tasks:
        log("\n5.1 跳过复验：本次没有实际完成的重跑任务")
        return []
    
    # 记录重跑次数
    log("\n5.1 记录重跑次数...")
    record_file = WORKSPACE_CONFIG['repair_counts_file']
    os.makedirs(os.path.dirname(record_file), exist_ok=True)
    counts = {}
    if os.path.exists(record_file):
        with open(record_file, 'r') as f:
            counts = json.load(f)
    
    today = datetime.now().strftime('%Y-%m-%d')
    for task in completed_tasks:
        table = task['table']
        if table not in counts:
            counts[table] = {}
        counts[table][today] = counts[table].get(today, 0) + 1
        log(f"  📝 {table}: 今日第{counts[table][today]}次")
    
    with open(record_file, 'w') as f:
        json.dump(counts, f, indent=2)
    
    # 执行复验
    log(f"\n5.2 执行复验工作流...")
    fuyan_workflows = select_fuyan_workflows(alerts)
    fuyan_results = []

    log(f"  选中复验工作流: {len(fuyan_workflows)} 个")
    if not fuyan_workflows:
        blocked_names = "、".join(sorted(BLOCKED_FUYAN_WORKFLOW_NAMES))
        if blocked_names:
            log(f"  ℹ️ 当前集群已禁用以下复验工作流，避免触发循环: {blocked_names}")
        return []
    
    for i, fuyan in enumerate(fuyan_workflows, 1):
        fuyan_name = get_fuyan_name(fuyan)
        fuyan_code = get_fuyan_code(fuyan)
        fuyan_project_code = get_fuyan_project_code(fuyan)
        fuyan_start_node = resolve_fuyan_start_node_code(fuyan)
        log(f"  [{i}] {fuyan_name}")
        
        data = {
            'processDefinitionCode': fuyan_code,
            'failureStrategy': 'CONTINUE',
            'warningType': 'NONE',
            'warningGroupId': 0,
            'processInstancePriority': 'MEDIUM',
            'workerGroup': 'default',
            'execType': 'START_PROCESS',
            'environmentCode': DS_ENVIRONMENT_CODE,
            'tenantCode': DS_TENANT_CODE,
            'runMode': 'RUN_MODE_SERIAL',
            'dryRun': 0,
        }
        if fuyan_start_node:
            data['startNodeList'] = fuyan_start_node
            data['taskDependType'] = 'TASK_ONLY'
        else:
            data['taskDependType'] = 'TASK_POST'

        success, result, msg, _, _, launched_at = start_workflow_instance_with_fallbacks(
            fuyan_project_code,
            fuyan_code,
            data,
            table=fuyan_name,
        )
        if success:
            instance_id = result.get('data')
            if isinstance(instance_id, list) and len(instance_id) > 0:
                instance_id = instance_id[0]
            log(f"    ✅ 启动成功: {instance_id}")
            fuyan_results.append({
                'name': fuyan_name,
                'id': instance_id,
                'status': 'success',
                'start_response_id': instance_id,
                'resolved_instance_id': None,
                'workflow_code': fuyan_code,
                'project_code': fuyan_project_code,
                'launched_at': launched_at,
            })
        else:
            error_msg = result.get('msg') or msg or '未知错误'
            log(f"    ❌ 启动失败: {error_msg}")
            fuyan_results.append({'name': fuyan_name, 'status': 'failed', 'error': error_msg})
    
    return fuyan_results


def wait_for_fuyan_results(fuyan_results, poll_interval=10, max_wait=60):
    """等待已启动的复验工作流完成，补充最终状态"""
    running_results = [
        dict(item)
        for item in fuyan_results
        if item.get('status') == 'success' and item.get('id')
    ]
    if not running_results:
        return fuyan_results

    start_time = time.time()
    pending = {str(item['id']): item for item in running_results}
    for item in pending.values():
        item['first_seen_at'] = time.time()

    while pending and (time.time() - start_time) < max_wait:
        still_pending = {}
        for instance_id, item in pending.items():
            current_instance_id = item.get('resolved_instance_id') or item.get('id')
            workflow_code = item.get('workflow_code')
            launched_at = item.get('launched_at')
            fuyan_project_code = item.get('project_code') or FUYAN_PROJECT_CODE
            discovered_instance = {}

            if not item.get('resolved_instance_id') and workflow_code:
                discovered_instance = find_recent_instance_by_workflow(
                    fuyan_project_code,
                    workflow_code,
                    launched_at=launched_at,
                )
                if discovered_instance.get('id') is not None:
                    resolved_instance_id = discovered_instance.get('id')
                    item['resolved_instance_id'] = resolved_instance_id
                    item['id'] = resolved_instance_id
                    current_instance_id = resolved_instance_id
                    debug_log(
                        f"复验实例发现真实实例 name={item.get('name')} start_response_id={item.get('start_response_id')} "
                        f"resolved_instance_id={resolved_instance_id}"
                    )

            success, data, msg = get_instance_detail(fuyan_project_code, current_instance_id)
            if not success or not data:
                fallback_data = get_instance_from_list(fuyan_project_code, current_instance_id)
                if fallback_data:
                    success = True
                    data = fallback_data
                    msg = ''
                elif discovered_instance:
                    success = True
                    data = discovered_instance
                    msg = ''
                elif workflow_code:
                    recent_instance = find_recent_instance_by_workflow(
                        fuyan_project_code,
                        workflow_code,
                        launched_at=launched_at,
                    )
                    if recent_instance:
                        if recent_instance.get('id') is not None:
                            item['resolved_instance_id'] = recent_instance.get('id')
                            item['id'] = recent_instance.get('id')
                        success = True
                        data = recent_instance
                        msg = ''
            if not success or not data:
                instance_age = time.time() - item.get('first_seen_at', start_time)
                diagnostics = collect_instance_query_diagnostics(
                    fuyan_project_code,
                    instance_id=item.get('id'),
                    workflow_code=workflow_code,
                    launched_at=launched_at,
                )
                debug_log(
                    f"复验实例查询失败 name={item.get('name')} instance_id={item.get('id')} "
                    f"diagnostics={json.dumps(diagnostics, ensure_ascii=False)}"
                )
                if instance_age >= max_wait:
                    item['final_status'] = 'timeout'
                    item['error'] = f"超过{max_wait}秒未获取到明确状态: {msg or '查询复验实例状态失败'}"
                else:
                    still_pending[str(item.get('id'))] = item
                continue

            data = maybe_replace_with_recent_real_instance(fuyan_project_code, item, data)
            state = data.get('state', 'UNKNOWN')
            if data.get('id') is not None:
                item['id'] = data.get('id')
                item['resolved_instance_id'] = data.get('id')
            item['state'] = state
            item['end_time'] = data.get('endTime')
            if state in ['SUCCESS', 'FINISHED']:
                item['final_status'] = 'success'
            elif state in ['FAILED', 'KILL', 'STOP']:
                item['final_status'] = 'failed'
                item['error'] = f"状态: {state}"
            else:
                still_pending[str(item.get('id'))] = item

        pending = still_pending
        if pending:
            log(f"  复验仍在等待: {len(pending)} 个，{poll_interval}秒后继续检查")
            time.sleep(poll_interval)

    for item in pending.values():
        item['final_status'] = 'timeout'
        item.setdefault('error', '等待复验结果超时')

    final_results = []
    running_by_id = {}
    for item in running_results:
        if item.get('id') is not None:
            running_by_id[str(item['id'])] = item
        if item.get('start_response_id') is not None:
            running_by_id[str(item['start_response_id'])] = item
    for item in fuyan_results:
        instance_id = item.get('id')
        if instance_id is not None and str(instance_id) in running_by_id:
            final_results.append(running_by_id[str(instance_id)])
        else:
            final_results.append(item)
    return final_results


def summarize_repair_outcome(alerts, completed_tasks, failed_tasks, manual_review_tasks, remaining_tables):
    """基于复验后的数据库状态汇总最终修复结果"""
    initial_alerts = []
    seen_tables = set()
    for alert in alerts:
        table = alert.get('table')
        if table and table not in seen_tables:
            seen_tables.add(table)
            initial_alerts.append(dict(alert))

    initial_by_table = {item['table']: item for item in initial_alerts}
    completed_by_table = {item['table']: item for item in completed_tasks if item.get('table')}
    failed_by_table = {item['table']: item for item in failed_tasks if item.get('table')}
    manual_by_table = {item['table']: item for item in manual_review_tasks if item.get('table')}

    rerun_tasks = []
    resolved_tasks = []
    remaining_tasks = []

    for alert in initial_alerts:
        table = alert['table']
        if table in completed_by_table or table in failed_by_table:
            rerun_task = dict(alert)
            rerun_task.update(completed_by_table.get(table, {}))
            if table in failed_by_table:
                rerun_task.update(failed_by_table[table])
            rerun_tasks.append(rerun_task)

        if table in failed_by_table:
            remaining_task = dict(alert)
            remaining_task.update(failed_by_table[table])
            remaining_task['result'] = 'manual_review'
            if not remaining_task.get('error'):
                remaining_task['error'] = '自动重跑失败，需人工处理'
            remaining_tasks.append(remaining_task)
            continue

        if table in manual_by_table:
            remaining_task = dict(alert)
            remaining_task.update(completed_by_table.get(table, {}))
            if table in failed_by_table:
                remaining_task.update(failed_by_table[table])
            remaining_task.update(manual_by_table[table])
            remaining_task['result'] = 'manual_review'
            if not remaining_task.get('error'):
                remaining_task['error'] = '需人工处理'
            remaining_tasks.append(remaining_task)
            continue

        if table not in remaining_tables:
            resolved_task = dict(alert)
            resolved_task.update(completed_by_table.get(table, {}))
            resolved_task['result'] = 'resolved'
            resolved_tasks.append(resolved_task)
            continue

        remaining_task = dict(alert)
        remaining_task.update(completed_by_table.get(table, {}))
        if table in failed_by_table:
            remaining_task.update(failed_by_table[table])
        if table in manual_by_table:
            remaining_task.update(manual_by_table[table])
        remaining_task['result'] = 'manual_review'
        if is_suspected_redundant_data(remaining_task):
            if not remaining_task.get('error'):
                remaining_task['error'] = build_redundant_data_manual_review_reason()
        else:
            if not remaining_task.get('error'):
                remaining_task['error'] = '复验完成后告警仍存在，需人工处理'
        remaining_tasks.append(remaining_task)

    return {
        'initial_alert_count': len(initial_alerts),
        'resolved_count': len(resolved_tasks),
        'remaining_count': len(remaining_tasks),
        'manual_review_count': len(remaining_tasks),
        'display_pending_tables_count': len(set(remaining_tables) | set(manual_by_table)),
        'rerun_tasks': rerun_tasks,
        'resolved_tasks': resolved_tasks,
        'remaining_tasks': remaining_tasks,
        'post_fuyan_remaining_tables': set(remaining_tables),
    }


def evaluate_repair_outcome(alerts, completed_tasks, failed_tasks, manual_review_tasks, fuyan_results):
    """等待复验完成后，再根据数据库回查结果判断最终修复成败"""
    log("\n5.3 等待复验完成并回查告警结果...")
    final_fuyan_results = wait_for_fuyan_results(fuyan_results)
    remaining_tables = get_remaining_alert_tables()
    log(f"  📋 复验后数据库仍未处理告警表: {len(remaining_tables)} 个")
    summary = summarize_repair_outcome(
        alerts=alerts,
        completed_tasks=completed_tasks,
        failed_tasks=failed_tasks,
        manual_review_tasks=manual_review_tasks,
        remaining_tables=remaining_tables,
    )
    return summary, final_fuyan_results


def step6_save_report(results, completed_tasks, failed_tasks, final_fuyan_results, summary, manual_review_tasks=None):
    """步骤6: 保存记录并发送TV报告"""
    if manual_review_tasks is None:
        manual_review_tasks = []

    log("\n" + "="*70)
    log("【步骤6】保存记录")
    log("="*70)
    
    record_dir = f"{AUTO_REPAIR_RECORDS_DIR}/{datetime.now().strftime('%Y-%m-%d')}"
    os.makedirs(record_dir, exist_ok=True)
    
    detail_file = f"{record_dir}/detail_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    summary_for_json = dict(summary)
    summary_for_json['post_fuyan_remaining_tables'] = sorted(summary['post_fuyan_remaining_tables'])
    with open(detail_file, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'results': results,
            'completed_tasks': completed_tasks,
            'failed_tasks': failed_tasks,
            'manual_review_tasks': manual_review_tasks,
            'fuyan_results': final_fuyan_results,
            'summary': summary_for_json,
        }, f, indent=2, ensure_ascii=False)
    
    log(f"  ✅ 记录已保存: {detail_file}")
    
    # 生成TV报告内容
    tv_report = generate_tv_report(summary, final_fuyan_results)
    
    # 发送TV报告到钉钉
    send_tv_report_to_dingtalk(tv_report)


def generate_tv_report(summary, fuyan_results):
    """生成TV格式报告"""
    report_lines = []
    report_lines.append("📺 【智能告警修复报告】")
    report_lines.append("")
    report_lines.append(f"⏰ 执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("")
    
    report_lines.append("📊 本次执行统计:")
    report_lines.append(f"  • 初始去重告警: {summary['initial_alert_count']} 个")
    report_lines.append(f"  • 复验后已消失: {summary['resolved_count']} 个")
    report_lines.append(f"  • 复验后仍存在: {summary['remaining_count']} 个")
    report_lines.append(f"  • 需人工处理: {summary['manual_review_count']} 个")
    report_lines.append(f"  • 复验启动: {len(fuyan_results)} 个")
    report_lines.append("")
    
    pending_tables_count = summary.get(
        'display_pending_tables_count',
        len(summary['post_fuyan_remaining_tables'])
    )
    report_lines.append(f"📋 当前未处理告警表: {pending_tables_count} 个")
    report_lines.append("")

    if summary.get('rerun_tasks'):
        report_lines.append("🔁 【本次已重跑任务】")
        for task in summary['rerun_tasks']:
            report_lines.append(f"  • {task['table']}")
            if task.get('instance_id'):
                report_lines.append(f"    实例ID: {task['instance_id']}")
            if task.get('end_time'):
                report_lines.append(f"    完成时间: {task['end_time']}")
            elif task.get('error'):
                report_lines.append(f"    结果: {task['error']}")
        report_lines.append("")
    
    if summary['resolved_tasks']:
        report_lines.append("✅ 【复验后已消失】")
        for task in summary['resolved_tasks']:
            report_lines.append(f"  • {task['table']}")
            if task.get('end_time'):
                report_lines.append(f"    完成时间: {task['end_time']}")
        report_lines.append("")
    
    if summary['remaining_tasks']:
        report_lines.append("⚠️ 【复验后仍存在，需人工处理】")
        for task in summary['remaining_tasks']:
            report_lines.append(f"  • {task['table']}")
            report_lines.append(f"    原因: {task.get('error', '复验完成后告警仍存在，需人工处理')}")
            if task.get('diff') not in (None, ''):
                report_lines.append(f"    数据量差异: {task['diff']}")
        report_lines.append("")
    
    report_lines.append("🔄 【复验工作流状态】")
    for fuyan in fuyan_results:
        final_status = fuyan.get('final_status')
        if final_status == 'success':
            report_lines.append(f"  ✅ {fuyan['name']}")
        elif final_status in ['failed', 'timeout', 'query_failed']:
            report_lines.append(f"  ❌ {fuyan['name']}")
            report_lines.append(f"     原因: {fuyan.get('error', final_status)}")
        elif fuyan.get('status') == 'success':
            report_lines.append(f"  ⏳ {fuyan['name']}")
            report_lines.append("     状态: 已启动，等待结果中")
        else:
            report_lines.append(f"  ❌ {fuyan['name']}")
            if fuyan.get('error'):
                report_lines.append(f"     错误: {fuyan['error']}")
        if fuyan.get('id'):
            report_lines.append(f"     实例ID: {fuyan['id']}")
    report_lines.append("")
    
    # 结尾
    report_lines.append("=" * 40)
    report_lines.append("📌 智能告警修复系统自动生成")
    
    return "\n".join(report_lines)


def send_tv_report_to_dingtalk(report_content):
    """发送TV报告到钉钉群"""
    log("\n" + "="*70)
    log("【发送TV报告到钉钉群】")
    log("="*70)
    
    try:
        # 保存报告到文件
        report_file = (
            f"{AUTO_REPAIR_RECORDS_DIR}/{datetime.now().strftime('%Y-%m-%d')}/"
            f"tv_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report_content)
        
        log(f"✅ TV报告已生成: {report_file}")
        
        # 优先直接调用TV API，避免依赖本机 openclaw 命令
        try:
            from core.send_tv_report import send_tv_report

            mentions_env = os.environ.get('TV_MENTIONS', '').strip()
            mentions = [m.strip() for m in mentions_env.split(',') if m.strip()]
            result = send_tv_report(report_content, mentions=mentions)

            if result.get('success'):
                log(f"✅ TV报告已直接发送到TV API (HTTP {result.get('status_code')})")
            else:
                log(
                    "⚠️ 直接发送TV API失败: "
                    f"HTTP {result.get('status_code')}, {result.get('response')}"
                )
        except Exception as e:
            log(f"⚠️ 尝试直接发送TV API失败: {e}")
        
        # 兜底：控制台输出，便于n8n继续采集日志
        print(f"\n{'='*50}")
        print("📺 TV告警修复报告")
        print(f"{'='*50}")
        print(report_content)
        print(f"{'='*50}\n")
        
        log("✅ TV报告已输出到控制台")
        
    except Exception as e:
        log(f"❌ 发送TV报告时出错: {e}")
        import traceback
        traceback.print_exc()
    
    log("\n" + "="*70)


def main():
    """主函数"""
    log("="*70)
    log("🚀 智能告警修复流程（v5.2 最终修复版）")
    log("="*70)
    log(f"⏰ 执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("")
    
    # 步骤1: 扫描告警
    alerts = step1_scan_alerts()
    if not alerts:
        log("\n✅ 没有需要处理的告警，流程结束")
        return
    
    # 步骤2: 查找工作流
    tasks = step2_find_locations(alerts)

    # 策略判断：疑似冗余数据告警首次允许重跑，后续转人工处理
    strategy_state = load_manual_review_state()
    out_of_window_tasks = []
    candidate_tasks = []
    for task in tasks:
        if task.get('status') == 'skipped_out_of_window':
            manual_task = dict(task)
            manual_task['status'] = 'skipped_manual_review'
            out_of_window_tasks.append(manual_task)
        else:
            candidate_tasks.append(task)

    runnable_tasks, manual_review_tasks = apply_repair_strategy(candidate_tasks, strategy_state)
    manual_review_tasks = out_of_window_tasks + manual_review_tasks
    
    # 步骤3-4: 统一启动修复并动态监控
    results, completed_tasks, failed_tasks = execute_repairs_in_batches(runnable_tasks)

    record_redundant_retry_attempt(strategy_state, completed_tasks)
    record_manual_review_tasks(strategy_state, manual_review_tasks)
    save_manual_review_state(strategy_state)

    if manual_review_tasks:
        log("\n⚠️ 以下任务已转人工处理，不再自动重跑:")
        for task in manual_review_tasks:
            log(f"  - {task['table']}: {task['error']}")

    results.extend(manual_review_tasks)

    if completed_tasks:
        # 步骤5: 执行复验
        fuyan_results = step5_execute_fuyan(completed_tasks, failed_tasks, alerts)

        summary, final_fuyan_results = evaluate_repair_outcome(
            alerts=alerts,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            manual_review_tasks=manual_review_tasks,
            fuyan_results=fuyan_results,
        )
    else:
        log("\n⚠️ 本次没有成功启动并完成的修复任务，跳过复验和复验回查")
        remaining_tables = get_remaining_alert_tables()
        summary = summarize_repair_outcome(
            alerts=alerts,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            manual_review_tasks=manual_review_tasks,
            remaining_tables=remaining_tables,
        )
        final_fuyan_results = []
    
    # 步骤6: 保存记录并发送TV报告
    step6_save_report(
        results,
        completed_tasks,
        failed_tasks,
        final_fuyan_results,
        summary,
        manual_review_tasks=manual_review_tasks,
    )
    
    log("\n" + "="*70)
    log("✅ 流程完成")
    log("="*70)


if __name__ == '__main__':
    main()
