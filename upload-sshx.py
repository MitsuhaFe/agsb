#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
import time
import signal
from pathlib import Path
import requests
from datetime import datetime
import tempfile
import threading
import queue
import re

# 配置
USER_NAME = "mitsuha_sshx_session"  # 可以自定义上传文件名称
UPLOAD_API = "https://file.zmkk.fun/api/upload"
USER_HOME = Path.home()
SSH_INFO_FILE = "ssh.txt"  # 可以自定义文件名
MAX_RETRIES = 3  # 最大重试次数
TIMEOUT_SECONDS = 60  # 超时时间设置为60秒
DEBUG = True  # 开启调试模式

def debug_log(message):
    """打印调试日志"""
    if DEBUG:
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f"[DEBUG {timestamp}] {message}")

class SSHXManager:
    def __init__(self):
        self.ssh_info_path = USER_HOME / SSH_INFO_FILE
        self.sshx_process = None
        self.session_info = {}
    
    def start_sshx_interactive(self):
        """交互式启动sshx（实时显示输出）并保持后台运行"""
        print("正在启动sshx（交互模式）...")
        
        # 尝试多次启动
        for attempt in range(1, MAX_RETRIES + 1):
            if attempt > 1:
                print(f"\n第 {attempt} 次尝试启动sshx...")
            
            try:
                # 使用管道执行命令，这样可以获取完整输出
                cmd = "curl -fsSL https://raw.githubusercontent.com/zhumengkang/agsb/main/get | sh -s run"
                print(f"执行命令: {cmd}")
                
                # 使用Popen进行实时输出
                debug_log("创建子进程...")
                process = subprocess.Popen(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                
                self.sshx_process = process  # 保存进程引用，保持后台运行
                output_lines = []
                link_found = False
                start_time = time.time()
                
                debug_log(f"开始读取输出，进程ID: {process.pid}")
                
                # 实时读取输出，找到链接后继续让进程在后台运行
                while True:
                    try:
                        line = process.stdout.readline()
                        
                        # 检查进程状态
                        poll_result = process.poll()
                        if poll_result is not None:
                            debug_log(f"进程已结束，返回码: {poll_result}")
                        
                        if not line and poll_result is not None:
                            # 命令执行完成，但可能需要等待链接出现
                            debug_log("命令执行完成，等待链接出现...")
                            print("命令执行完成，等待链接出现...")
                            
                            # 检查是否有任何输出包含关键字
                            debug_log(f"检查已收集的 {len(output_lines)} 行输出中是否有链接")
                            for i, saved_line in enumerate(output_lines):
                                debug_log(f"行 {i}: {saved_line}")
                                if "Link:" in saved_line or "➜" in saved_line:
                                    debug_log(f"发现可能包含链接的行: {saved_line}")
                            
                            # 额外等待10秒，因为sshx可能需要一些时间才会输出链接
                            extra_wait = 10
                            debug_log(f"额外等待 {extra_wait} 秒...")
                            for i in range(extra_wait):
                                time.sleep(1)
                                debug_log(f"等待中... {i+1}/{extra_wait}")
                                # 检查是否有新输出
                                try:
                                    new_line = process.stdout.readline()
                                    if new_line:
                                        new_line = new_line.rstrip()
                                        debug_log(f"新输出: {new_line}")
                                        print(new_line)
                                        output_lines.append(new_line)
                                        # 检查是否包含链接
                                        if self.check_for_link(new_line, output_lines):
                                            link_found = True
                                            break
                                except Exception as e:
                                    debug_log(f"读取新输出时出错: {e}")
                            
                            if not link_found:
                                debug_log("命令执行完成，但未找到链接")
                                print("命令执行完成，但未找到链接")
                            break
                        
                        if line:
                            line = line.rstrip()
                            print(line)  # 实时显示
                            output_lines.append(line)
                            debug_log(f"读取到输出: {line}")
                            
                            # 检查是否包含链接
                            if self.check_for_link(line, output_lines):
                                link_found = True
                                break
                        
                        # 检查是否超时
                        elapsed = time.time() - start_time
                        if elapsed > TIMEOUT_SECONDS:
                            debug_log(f"等待超时 ({elapsed:.1f}秒)")
                            print(f"\n⚠ 等待链接超时（{TIMEOUT_SECONDS}秒）")
                            # 尝试从已有输出中查找链接
                            debug_log("检查所有已收集的输出中是否有链接")
                            for line in output_lines:
                                if self.check_for_link(line, output_lines):
                                    link_found = True
                                    break
                            break
                    
                    except Exception as e:
                        debug_log(f"读取输出时出错: {e}")
                        print(f"读取输出时出错: {e}")
                        break
                
                # 命令执行完成后，再次检查所有输出是否包含链接
                if not link_found and process.poll() is not None:
                    debug_log("命令已完成，检查完整输出中的链接...")
                    print("命令已完成，检查完整输出中的链接...")
                    
                    # 打印所有收集到的输出
                    debug_log("所有收集到的输出:")
                    for i, line in enumerate(output_lines):
                        debug_log(f"{i}: {line}")
                        if "sshx" in line.lower() or "link" in line.lower() or "➜" in line:
                            debug_log(f"  ↑ 可能相关的行")
                    
                    for line in output_lines:
                        if self.check_for_link(line, output_lines):
                            link_found = True
                            break
                
                if link_found:
                    debug_log("成功找到链接!")
                    return True
                
                # 如果是最后一次尝试，则不杀死进程，让它继续运行
                if attempt < MAX_RETRIES:
                    debug_log(f"尝试 {attempt} 失败，正在终止当前进程...")
                    print(f"尝试 {attempt} 失败，正在终止当前进程...")
                    if process.poll() is None:
                        try:
                            process.terminate()
                            time.sleep(1)
                        except:
                            pass
                
            except Exception as e:
                debug_log(f"交互式启动sshx失败: {e}")
                print(f"✗ 交互式启动sshx失败: {e}")
                if attempt < MAX_RETRIES:
                    print("准备重试...")
                    time.sleep(2)  # 等待2秒再重试
        
        debug_log(f"已尝试 {MAX_RETRIES} 次，仍未能获取sshx链接")
        print(f"已尝试 {MAX_RETRIES} 次，仍未能获取sshx链接")
        
        # 尝试直接执行命令并打印结果
        debug_log("尝试直接执行命令并获取结果...")
        print("\n尝试直接执行命令并获取结果...")
        try:
            direct_result = subprocess.run(
                "curl -fsSL https://raw.githubusercontent.com/zhumengkang/agsb/main/get | sh -s run",
                shell=True,
                capture_output=True,
                text=True,
                timeout=60
            )
            debug_log(f"直接执行命令返回码: {direct_result.returncode}")
            debug_log("直接执行命令输出:")
            debug_log(direct_result.stdout)
            
            # 检查输出中是否包含链接
            for line in direct_result.stdout.split('\n'):
                if "Link:" in line or "➜" in line:
                    debug_log(f"发现可能包含链接的行: {line}")
                    # 尝试提取链接
                    match = re.search(r'https://sshx\.io/s/[^\s#]+(?:#[^\s]*)?', line)
                    if match:
                        link = match.group(0)
                        self.session_info['link'] = link
                        debug_log(f"从直接执行中找到链接: {link}")
                        print(f"\n✓ 从直接执行中找到链接: {link}")
                        return True
            
            print("直接执行命令也未能找到链接")
        except Exception as e:
            debug_log(f"直接执行命令失败: {e}")
            print(f"直接执行命令失败: {e}")
        
        return False
    
    def check_for_link(self, line, output_lines=None):
        """检查行中是否包含sshx链接"""
        # 检查当前行
        if "Link:" in line or "➜  Link:" in line:
            debug_log(f"发现可能包含链接的行: {line}")
            # 尝试提取链接
            match = re.search(r'https://sshx\.io/s/[^\s#]+(?:#[^\s]*)?', line)
            if match:
                link = match.group(0)
                self.session_info['link'] = link
                debug_log(f"提取到链接: {link}")
                print(f"\n✓ 已获取sshx链接: {link}")
                print("✓ sshx将继续在后台运行...")
                return True
            else:
                debug_log(f"行包含'Link:'但未找到链接URL: {line}")
        
        # 如果当前行包含关键词但没有完整链接，检查下一行
        if output_lines and ("Link:" in line or "➜  Link:" in line) and len(output_lines) > 1:
            current_index = output_lines.index(line)
            if current_index < len(output_lines) - 1:
                next_line = output_lines[current_index + 1]
                debug_log(f"检查下一行是否包含链接: {next_line}")
                match = re.search(r'https://sshx\.io/s/[^\s#]+(?:#[^\s]*)?', next_line)
                if match:
                    link = match.group(0)
                    self.session_info['link'] = link
                    debug_log(f"在下一行找到链接: {link}")
                    print(f"\n✓ 已获取sshx链接: {link}")
                    print("✓ sshx将继续在后台运行...")
                    return True
        
        return False
    
    def save_ssh_info(self):
        """保存SSH信息到文件"""
        try:
            content = f"""SSHX 会话信息
创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

"""
            
            if 'link' in self.session_info:
                content += f"SSHX Link: {self.session_info['link']}\n"
            
            with open(self.ssh_info_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            debug_log(f"SSH信息已保存到: {self.ssh_info_path}")
            print(f"✓ SSH信息已保存到: {self.ssh_info_path}")
            return True
            
        except Exception as e:
            debug_log(f"保存SSH信息失败: {e}")
            print(f"✗ 保存SSH信息失败: {e}")
            return False
    
    def upload_to_api(self, user_name=USER_NAME):
        """上传SSH信息文件到API"""
        try:
            if not self.ssh_info_path.exists():
                debug_log("SSH信息文件不存在")
                print("✗ SSH信息文件不存在")
                return False
            
            debug_log(f"开始上传到API: {UPLOAD_API}")
            print("正在上传SSH信息到API...")
            
            # 读取文件内容
            with open(self.ssh_info_path, 'r', encoding='utf-8') as f:
                content = f.read()
                debug_log(f"文件内容: {content}")
            
            # 创建临时文件用于上传
            file_name = f"{user_name}.txt"
            temp_file = USER_HOME / file_name
            debug_log(f"创建临时文件: {temp_file}")
            
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # 上传文件
            debug_log("开始上传文件...")
            with open(temp_file, 'rb') as f:
                files = {'file': (file_name, f)}
                response = requests.post(UPLOAD_API, files=files, timeout=30)
            
            debug_log(f"API响应状态码: {response.status_code}")
            
            # 删除临时文件
            if temp_file.exists():
                temp_file.unlink()
                debug_log("临时文件已删除")
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    debug_log(f"API响应JSON: {result}")
                    if result.get('success') or result.get('url'):
                        url = result.get('url', '')
                        debug_log(f"上传成功，URL: {url}")
                        print(f"✓ 文件上传成功!")
                        print(f"  上传URL: {url}")
                        
                        # 保存URL到文件
                        url_file = USER_HOME / "ssh_upload_url.txt"
                        with open(url_file, 'w') as f:
                            f.write(url)
                        debug_log(f"URL已保存到: {url_file}")
                        print(f"  URL已保存到: {url_file}")
                        return True
                    else:
                        debug_log(f"API返回错误: {result}")
                        print(f"✗ API返回错误: {result}")
                        print(f"原始响应: {response.text}")
                        return False
                except Exception as e:
                    debug_log(f"解析API响应失败: {e}")
                    print(f"✗ 解析API响应失败: {e}")
                    print(f"原始响应: {response.text}")
                    return False
            else:
                debug_log(f"上传失败，状态码: {response.status_code}")
                print(f"✗ 上传失败，状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return False
                
        except Exception as e:
            debug_log(f"上传到API失败: {e}")
            print(f"✗ 上传到API失败: {e}")
            return False
    
    def manual_input_link(self):
        """手动输入链接"""
        try:
            debug_log("提示用户手动输入链接")
            print("\n由于自动获取链接失败，请手动输入SSHX链接:")
            print("请先在另一个终端执行: curl -fsSL https://raw.githubusercontent.com/zhumengkang/agsb/main/get | sh -s run")
            print("然后将输出中的链接复制到这里")
            
            while True:
                link = input("请输入SSHX链接 (https://sshx.io/s/... 或输入 q 退出): ").strip()
                debug_log(f"用户输入: {link}")
                
                if link.lower() == 'q':
                    debug_log("用户选择退出")
                    return False
                
                if not link:
                    debug_log("链接为空")
                    print("链接不能为空，请重新输入")
                    continue
                
                if "sshx.io" in link and link.startswith("https://"):
                    self.session_info['link'] = link
                    debug_log(f"接受的链接: {link}")
                    print(f"✓ 已记录SSHX链接: {link}")
                    return True
                else:
                    debug_log(f"无效的链接: {link}")
                    print("✗ 无效的链接，请输入正确的sshx.io链接")
                    
        except KeyboardInterrupt:
            debug_log("用户取消输入")
            print("\n用户取消输入")
            return False
        except Exception as e:
            debug_log(f"手动输入链接失败: {e}")
            print(f"✗ 手动输入链接失败: {e}")
            return False
    
    def cleanup(self):
        """清理资源但保持sshx后台运行"""
        if self.sshx_process and self.sshx_process.poll() is None:
            debug_log(f"sshx进程继续在后台运行，PID: {self.sshx_process.pid}")
            print("✓ sshx进程继续在后台运行")
            print(f"  进程ID: {self.sshx_process.pid}")
            print("  如需停止sshx，请手动执行: pkill -f sshx")
        debug_log("Python脚本资源清理完成")
        print("✓ Python脚本资源清理完成")

def signal_handler(signum, frame):
    """信号处理器"""
    print("\n收到退出信号，正在清理...")
    if hasattr(signal_handler, 'manager'):
        signal_handler.manager.cleanup()
    sys.exit(0)

def main():
    debug_log("脚本开始执行")
    manager = SSHXManager()
    
    # 只在主线程中注册信号处理器
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        signal_handler.manager = manager  # 保存引用用于信号处理
        debug_log("信号处理器已注册")
    except ValueError:
        # 如果不在主线程中（如Streamlit环境），跳过信号处理器注册
        debug_log("检测到非主线程环境，跳过信号处理器注册")
        print("⚠ 检测到非主线程环境，跳过信号处理器注册")
    
    try:
        print("=== SSHX 会话管理器 ===")
        debug_log("SSHX会话管理器初始化")
        
        # 检查并安装依赖
        try:
            import requests
            debug_log("requests库已安装")
        except ImportError:
            debug_log("检测到未安装requests库，开始安装")
            print("检测到未安装requests库，正在安装...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
            import requests
            debug_log("requests库安装成功")
            print("✓ requests库安装成功")
        
        # 直接使用交互式方法启动sshx
        debug_log("开始交互式启动sshx")
        sshx_success = manager.start_sshx_interactive()
        debug_log(f"交互式启动结果: {sshx_success}")
        
        # 如果自动启动失败，提示手动输入
        if not sshx_success:
            debug_log("自动启动失败，提示手动输入")
            print("\n自动启动失败，请手动输入链接")
            if not manager.manual_input_link():
                debug_log("用户选择退出或输入失败")
                print("用户选择退出或输入失败")
                return False
        
        # 保存SSH信息
        debug_log("开始保存SSH信息")
        if not manager.save_ssh_info():
            debug_log("保存SSH信息失败，但继续尝试上传")
            print("⚠ 保存SSH信息失败，但继续尝试上传")
        
        # 上传到API
        debug_log(f"开始上传SSH信息到API: {UPLOAD_API}")
        print(f"\n开始上传SSH信息到API: {UPLOAD_API}")
        upload_success = manager.upload_to_api(USER_NAME)
        debug_log(f"上传结果: {upload_success}")
        if not upload_success:
            debug_log("上传失败，但本地文件已保存")
            print("⚠ 上传失败，但本地文件已保存")
            print("请检查网络连接和API地址是否正确")
        
        debug_log("操作完成")
        print("\n=== 操作完成 ===")
        print(f"✓ 会话信息已保存到: {manager.ssh_info_path}")
        
        if upload_success:
            url_file = USER_HOME / "ssh_upload_url.txt"
            if url_file.exists():
                with open(url_file, 'r') as f:
                    upload_url = f.read().strip()
                debug_log(f"上传URL: {upload_url}")
                print(f"✓ 上传URL: {upload_url}")
        
        # 打印SSHX链接地址
        if 'link' in manager.session_info:
            debug_log(f"SSHX链接: {manager.session_info['link']}")
            print("\n=== SSHX 连接信息 ===")
            print(f"SSHX 链接: {manager.session_info['link']}")
            print("现在你可以使用这个链接进行远程连接了！")
            print("⚠ 重要：sshx将继续在后台运行，请不要关闭终端")
        
        debug_log("脚本执行完成")
        print("\n🎉 脚本执行完成！sshx正在后台运行中...")
        
        return True
            
    except Exception as e:
        debug_log(f"程序执行出错: {e}")
        print(f"✗ 程序执行出错: {e}")
        import traceback
        debug_log(traceback.format_exc())
        traceback.print_exc()
        return False
    finally:
        debug_log("执行清理操作")
        manager.cleanup()
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
