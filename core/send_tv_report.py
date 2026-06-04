#!/usr/bin/env python3
"""
TV Alert API 报告发送脚本
用于向钉钉机器人发送智能告警修复报告

API地址: https://tv-service-alert.kuainiu.chat/alert/v2/array
"""

import json
import urllib.request
import urllib.error
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.config import TV_CONFIG

# TV API配置
TV_API_URL = TV_CONFIG['api_url']
TV_BOT_ID = TV_CONFIG['bot_id']
TV_APP_ID = TV_CONFIG['app_id']


def send_tv_report(message, mentions=None, bot_id=None):
    """
    发送报告到TV Alert API
    
    Args:
        message: 报告消息内容（字符串）
        mentions: @的人员邮箱列表，如 ["user@company.com"]
    
    Returns:
        dict: {'success': True/False, 'status_code': int, 'response': str}
    """
    if mentions is None:
        mentions = []
    
    # TV API 实测要求顶层必须包含 message 字段。
    payload = {
        'appId': TV_APP_ID,
        'botId': bot_id or TV_BOT_ID,
        'message': message,
        'mentions': mentions,
    }
    
    # 转换为JSON
    json_data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    
    # 创建请求
    req = urllib.request.Request(
        TV_API_URL,
        data=json_data,
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        },
        method='POST'
    )
    
    try:
        # 发送请求
        with urllib.request.urlopen(req, timeout=30) as response:
            status_code = response.getcode()
            response_body = response.read().decode('utf-8')
            
            if 200 <= status_code < 300:
                return {
                    'success': True,
                    'status_code': status_code,
                    'response': response_body
                }
            else:
                return {
                    'success': False,
                    'status_code': status_code,
                    'response': response_body
                }
                
    except urllib.error.HTTPError as e:
        response_body = ''
        if e.fp is not None:
            try:
                response_body = e.fp.read().decode('utf-8')
            except Exception:
                response_body = ''
        return {
            'success': False,
            'status_code': e.code,
            'response': response_body or str(e.reason)
        }
    except Exception as e:
        return {
            'success': False,
            'status_code': None,
            'response': str(e)
        }


def format_repair_report(fixed_tables, failed_tables, fuyan_results, execution_time=None):
    """
    格式化修复报告
    
    Args:
        fixed_tables: 修复成功的表列表
        failed_tables: 修复失败的表列表
        fuyan_results: 复验结果列表
        execution_time: 执行时间字符串
    
    Returns:
        str: 格式化后的报告内容
    """
    lines = []
    lines.append("📊 智能告警修复报告")
    lines.append("")
    
    if execution_time:
        lines.append(f"⏰ 执行时间: {execution_time}")
        lines.append("")
    
    # 成功的表
    if fixed_tables:
        lines.append(f"✅ 修复成功 ({len(fixed_tables)}个表):")
        for table_info in fixed_tables:
            table_name = table_info.get('table', '未知')
            dt = table_info.get('dt', '未知')
            instance_id = table_info.get('instance_id', 'N/A')
            lines.append(f"  • {table_name}")
            lines.append(f"    dt={dt}, 实例ID: {instance_id}")
        lines.append("")
    
    # 失败的表
    if failed_tables:
        lines.append(f"❌ 修复失败 ({len(failed_tables)}个表):")
        for table_info in failed_tables:
            table_name = table_info.get('table', '未知')
            dt = table_info.get('dt', '未知')
            error = table_info.get('error', '未知错误')
            lines.append(f"  • {table_name} (dt={dt})")
            lines.append(f"    错误: {error}")
        lines.append("")
    
    # 复验情况
    fuyan_success = sum(1 for f in fuyan_results if f.get('status') == 'success')
    fuyan_total = len(fuyan_results)
    lines.append(f"🔄 复验执行: {fuyan_success}/{fuyan_total} 个工作流启动成功")
    
    return "\n".join(lines)


def main():
    """测试发送"""
    if len(sys.argv) < 2:
        print("用法: python3 send_tv_report.py '消息内容'")
        print("或: python3 send_tv_report.py --test")
        return
    
    if sys.argv[1] == '--test':
        # 测试模式
        report = """📊 智能告警修复报告测试

⏰ 执行时间: 2026-03-25 14:00:00

✅ 修复成功 (2个表):
  • dwd_asset_account_repay
    dt=2026-03-23, 实例ID: 123456789
  • dwb_asset_period_info
    dt=2026-03-22, 实例ID: 987654321

🔄 复验执行: 6/6 个工作流启动成功

💾 记录保存完成
"""
        result = send_tv_report(report)
    else:
        # 直接使用命令行参数作为消息
        result = send_tv_report(sys.argv[1])
    
    # 输出结果
    if result['success']:
        print(f"✅ 发送成功 (HTTP {result['status_code']})")
    else:
        print(f"❌ 发送失败 (HTTP {result['status_code']})")
        print(f"错误: {result['response']}")
    
    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
