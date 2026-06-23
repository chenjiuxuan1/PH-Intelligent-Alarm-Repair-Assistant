#!/usr/bin/env python3
"""
环境变量自动加载模块
在脚本启动时自动从 ~/.bashrc 加载环境变量
"""

import os
import re


SUPPORTED_ENV_VARS = {
    'APP_WORKSPACE',
    'SCHEDULE_EXPORT_CSV',
    'DS_BASE_URL',
    'DS_TOKEN',
    'DS_PROJECT_CODE',
    'DS_FUYAN_PROJECT_CODE',
    'DS_FUYAN_PROJECT_NAME',
    'DS_ENVIRONMENT_CODE',
    'DS_TENANT_CODE',
    'FUYAN_WORKFLOWS_JSON',
    'DB_HOST',
    'DB_PORT',
    'DB_USER',
    'DB_PASSWORD',
    'DB_NAME',
    'OPENCLAW_WEBHOOK',
    'OPENCLAW_HOOK_TOKEN',
    'TV_API_URL',
    'TV_BOT_ID',
    'TV_APP_ID',
    'TV_MENTIONS',
    'QUALITY_RESULT_TABLE',
    'QUALITY_ALERT_TABLE',
}

def load_bashrc_env():
    """
    从 ~/.bashrc 文件读取 export 语句并设置环境变量
    """
    # 如果环境变量已设置，无需重复加载
    if os.environ.get('DS_TOKEN') and os.environ.get('DB_PASSWORD'):
        return
    
    bashrc_path = os.path.expanduser('~/.bashrc')
    
    if not os.path.exists(bashrc_path):
        print(f"⚠️ 警告: {bashrc_path} 不存在")
        return
    
    try:
        with open(bashrc_path, 'r') as f:
            content = f.read()
        
        # 查找 export 语句
        # 匹配格式: export VAR_NAME='value' 或 export VAR_NAME="value" 或 export VAR_NAME=value
        pattern = r"export\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*['\"]?([^'\"\n]*)['\"]?"
        matches = re.findall(pattern, content)
        
        loaded_count = 0
        for var_name, value in matches:
            # 只加载关键的环境变量
            if var_name in SUPPORTED_ENV_VARS:
                if value and not os.environ.get(var_name):
                    os.environ[var_name] = value
                    loaded_count += 1
        
        if loaded_count > 0:
            print(f"✅ 已从 {bashrc_path} 加载 {loaded_count} 个环境变量")
            
    except Exception as e:
        print(f"⚠️ 加载 .bashrc 环境变量失败: {e}")


# 模块导入时自动加载
load_bashrc_env()
