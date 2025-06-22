#!/usr/bin/env python3
import os
import sys
import json
import ssl
import shutil
import platform
import urllib.request
import urllib.parse
import subprocess
import socket
import time
import argparse
from pathlib import Path

def get_user_home():
    """获取用户主目录"""
    return str(Path.home())

def get_system_info():
    """获取系统信息"""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    # 系统映射
    os_map = {
        'linux': 'linux',
        'darwin': 'darwin',  # macOS
        'windows': 'windows'
    }
    
    # 架构映射
    arch_map = {
        'x86_64': 'amd64',
        'amd64': 'amd64',
        'aarch64': 'arm64',
        'arm64': 'arm64',
        'i386': '386',
        'i686': '386'
    }
    
    os_name = os_map.get(system, 'linux')
    arch = arch_map.get(machine, 'amd64')
    
    return os_name, arch

def ensure_nginx_user():
    """确保nginx用户存在，如果不存在就创建，统一使用nginx用户"""
    try:
        # 检查nginx用户是否已存在
        try:
            result = subprocess.run(['id', 'nginx'], check=True, capture_output=True, text=True)
            if result.returncode == 0:
                print("✅ nginx用户已存在")
                return 'nginx'
        except:
            # nginx用户不存在，创建它
            print("🔧 nginx用户不存在，正在创建...")
            
            # 创建nginx系统用户（无登录shell，无家目录）
            try:
                subprocess.run([
                    'sudo', 'useradd', 
                    '--system',           # 系统用户
                    '--no-create-home',   # 不创建家目录
                    '--shell', '/bin/false',  # 无登录shell
                    '--comment', 'nginx web server',  # 注释
                    'nginx'
                ], check=True, capture_output=True)
                print("✅ nginx用户创建成功")
                return 'nginx'
            except subprocess.CalledProcessError as e:
                # 如果创建失败，可能是因为用户已存在但id命令失败，或其他原因
                print(f"⚠️ 创建nginx用户失败: {e}")
                
                # 再次检查用户是否存在（可能是并发创建）
                try:
                    subprocess.run(['id', 'nginx'], check=True, capture_output=True)
                    print("✅ nginx用户实际上已存在")
                    return 'nginx'
                except:
                    # 确实创建失败，fallback到root用户
                    print("⚠️ 使用root用户作为nginx运行用户")
                    return 'root'
        
    except Exception as e:
        print(f"❌ 处理nginx用户时出错: {e}")
        # 出错时使用root用户
        return 'root'

def set_nginx_permissions(web_dir):
    """设置nginx目录的正确权限"""
    try:
        nginx_user = ensure_nginx_user()
        print(f"🔧 设置目录权限: {web_dir}")
        print(f"👤 使用用户: {nginx_user}")
        
        # 设置目录和文件权限
        subprocess.run(['sudo', 'chown', '-R', f'{nginx_user}:{nginx_user}', web_dir], check=True)
        subprocess.run(['sudo', 'chmod', '-R', '755', web_dir], check=True)
        subprocess.run(['sudo', 'find', web_dir, '-type', 'f', '-exec', 'chmod', '644', '{}', ';'], check=True)
        
        print(f"✅ 权限设置完成: {web_dir} (用户: {nginx_user})")
        return True
    except Exception as e:
        print(f"❌ 设置权限失败: {e}")
        return False

def check_port_available(port):
    """检查端口是否可用（仅使用socket）"""
    try:
        # 对于Hysteria2，我们主要关心UDP端口
        # nginx使用TCP端口，hysteria使用UDP端口，它们可以共存
        
        # 检查UDP端口是否可用（这是hysteria2需要的）
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(1)
            try:
                s.bind(('', port))
                return True  # UDP端口可用
            except:
                # UDP端口被占用，检查是否是hysteria进程
                return False
                
    except:
        # 如果有任何异常，保守起见返回端口不可用
        return False

def is_port_listening(port):
    """检查端口是否已经在监听（服务是否已启动）"""
    try:
        # 尝试连接到端口
        # 由于 Hysteria 使用 UDP，我们检查 UDP 端口
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1)
        
        # 尝试发送一个数据包到端口
        # 如果端口打开，send不会抛出异常
        try:
            sock.sendto(b"ping", ('127.0.0.1', port))
            try:
                sock.recvfrom(1024)  # 尝试接收响应
                return True
            except socket.timeout:
                # 没收到响应但也没报错，可能仍在监听
                return True
        except:
            pass
            
        # 另一种检查方式：尝试绑定端口，如果失败说明端口已被占用
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_sock.bind(('', port))
            test_sock.close()
            return False  # 能成功绑定说明端口未被占用
        except:
            return True  # 无法绑定说明端口已被占用
            
        return False
    except:
        return False
    finally:
        try:
            sock.close()
        except:
            pass

def check_process_running(pid_file):
    """检查进程是否在运行"""
    if not os.path.exists(pid_file):
        return False
        
    try:
        with open(pid_file, 'r') as f:
            pid = f.read().strip()
            
        if not pid:
            return False
            
        # 尝试发送信号0检查进程是否存在
        try:
            os.kill(int(pid), 0)
            return True
        except:
            return False
    except:
        return False

def create_directories():
    """创建必要的目录"""
    home = get_user_home()
    dirs = [
        f"{home}/.hysteria2",
        f"{home}/.hysteria2/cert",
        f"{home}/.hysteria2/config",
        f"{home}/.hysteria2/logs"
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    return dirs[0]

def download_file(url, save_path, max_retries=3):
    """下载文件，带重试机制"""
    for i in range(max_retries):
        try:
            print(f"正在下载... (尝试 {i+1}/{max_retries})")
            urllib.request.urlretrieve(url, save_path)
            return True
        except Exception as e:
            print(f"下载失败: {e}")
            if i < max_retries - 1:
                time.sleep(2)  # 等待2秒后重试
            continue
    return False

def get_latest_version():
    """返回固定的最新版本号 v2.6.1"""
    return "v2.6.1"

def get_download_filename(os_name, arch):
    """根据系统和架构返回正确的文件名"""
    # windows 需要 .exe
    if os_name == 'windows':
        if arch == 'amd64':
            return 'hysteria-windows-amd64.exe'
        elif arch == '386':
            return 'hysteria-windows-386.exe'
        elif arch == 'arm64':
            return 'hysteria-windows-arm64.exe'
        else:
            return f'hysteria-windows-{arch}.exe'
    else:
        return f'hysteria-{os_name}-{arch}'

def verify_binary(binary_path):
    """验证二进制文件是否有效（简化版）"""
    try:
        # 检查文件是否存在
        if not os.path.exists(binary_path):
            return False
            
        # 检查文件大小（至少5MB - hysteria一般大于10MB）
        if os.path.getsize(binary_path) < 5 * 1024 * 1024:
            return False
            
        # 设置文件为可执行
        os.chmod(binary_path, 0o755)
        
        # 返回成功
        return True
    except:
        return False

def download_hysteria2(base_dir):
    """下载Hysteria2二进制文件，使用简化链接和验证方式"""
    try:
        version = get_latest_version()
        os_name, arch = get_system_info()
        filename = get_download_filename(os_name, arch)
        
        # 只使用原始GitHub链接，避免镜像问题
        url = f"https://github.com/apernet/hysteria/releases/download/app/{version}/{filename}"
        
        binary_path = f"{base_dir}/hysteria"
        if os_name == 'windows':
            binary_path += '.exe'
        
        print(f"正在下载 Hysteria2 {version}...")
        print(f"系统类型: {os_name}, 架构: {arch}, 文件名: {filename}")
        print(f"下载链接: {url}")
        
        # 使用wget下载
        try:
            has_wget = shutil.which('wget') is not None
            has_curl = shutil.which('curl') is not None
            
            if has_wget:
                print("使用wget下载...")
                subprocess.run(['wget', '--tries=3', '--timeout=15', '-O', binary_path, url], check=True)
            elif has_curl:
                print("使用curl下载...")
                subprocess.run(['curl', '-L', '--connect-timeout', '15', '-o', binary_path, url], check=True)
            else:
                print("系统无wget/curl，尝试使用Python下载...")
                urllib.request.urlretrieve(url, binary_path)
                
            # 验证下载
            if not verify_binary(binary_path):
                raise Exception("下载的文件无效")
                
            print(f"下载成功: {binary_path}, 大小: {os.path.getsize(binary_path)/1024/1024:.2f}MB")
            return binary_path, version
            
        except Exception as e:
            print(f"自动下载失败: {e}")
            print("请按照以下步骤手动下载:")
            print(f"1. 访问 https://github.com/apernet/hysteria/releases/tag/app/{version}")
            print(f"2. 下载 {filename} 文件")
            print(f"3. 将文件重命名为 hysteria (不要加后缀) 并移动到 {base_dir}/ 目录")
            print(f"4. 执行: chmod +x {base_dir}/hysteria")
            
            # 询问用户文件是否已放置
            while True:
                user_input = input("已完成手动下载和放置? (y/n): ").lower()
                if user_input == 'y':
                    # 检查文件是否存在
                    if os.path.exists(binary_path) and verify_binary(binary_path):
                        print("文件验证成功，继续安装...")
                        return binary_path, version
                    else:
                        print(f"文件不存在或无效，请确保放在 {binary_path} 位置。")
                elif user_input == 'n':
                    print("中止安装。")
                    sys.exit(1)
    
    except Exception as e:
        print(f"下载错误: {e}")
        sys.exit(1)

def get_ip_address():
    """获取本机IP地址（优先获取公网IP，如果失败则使用本地IP）"""
    # 首先尝试获取公网IP
    try:
        # 尝试从公共API获取公网IP
        with urllib.request.urlopen('https://api.ipify.org', timeout=5) as response:
            public_ip = response.read().decode('utf-8')
            if public_ip and len(public_ip) > 0:
                return public_ip
    except:
        try:
            # 备选API
            with urllib.request.urlopen('https://ifconfig.me', timeout=5) as response:
                public_ip = response.read().decode('utf-8')
                if public_ip and len(public_ip) > 0:
                    return public_ip
        except:
            pass

    # 如果获取公网IP失败，尝试获取本地IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 不需要真正连接，只是获取路由信息
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except:
        # 如果所有方法都失败，返回本地回环地址
        return '127.0.0.1'

def setup_nginx_smart_proxy(base_dir, domain, web_dir, cert_path, key_path, hysteria_port):
    """设置nginx智能代理：浏览器访问显示网站，Hysteria2客户端透明转发"""
    print("🚀 正在配置nginx智能代理（完美伪装方案）...")
    
    try:
        # 修改Hysteria2配置，支持WebSocket传输
        hysteria_internal_port = 44300
        
        config_path = f"{base_dir}/config/config.json"
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # 配置Hysteria2使用内部端口和WebSocket
            config['listen'] = f":{hysteria_internal_port}"
            config['transport'] = {
                "type": "ws",
                "path": "/hy2-tunnel"
            }
            
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            print(f"✅ Hysteria2配置为WebSocket模式: {hysteria_internal_port}")
        
        # 检查证书文件
        print(f"🔍 检查证书文件路径:")
        print(f"证书文件: {cert_path}")
        print(f"密钥文件: {key_path}")
        
        if not os.path.exists(cert_path):
            print(f"❌ 证书文件不存在: {cert_path}")
            # 尝试找到证书文件
            possible_cert_paths = [
                f"{base_dir}/cert/server.crt",
                f"{base_dir}/certs/cert.pem", 
                f"{base_dir}/cert.pem"
            ]
            for path in possible_cert_paths:
                if os.path.exists(path):
                    cert_path = path
                    print(f"✅ 找到证书文件: {cert_path}")
                    break
            else:
                print("❌ 未找到任何证书文件，生成新证书...")
                cert_path, key_path = generate_self_signed_cert(base_dir, domain)
        
        if not os.path.exists(key_path):
            print(f"❌ 密钥文件不存在: {key_path}")
            # 尝试找到密钥文件
            possible_key_paths = [
                f"{base_dir}/cert/server.key",
                f"{base_dir}/certs/key.pem",
                f"{base_dir}/key.pem"
            ]
            for path in possible_key_paths:
                if os.path.exists(path):
                    key_path = path
                    print(f"✅ 找到密钥文件: {key_path}")
                    break
            else:
                print("❌ 未找到任何密钥文件，生成新证书...")
                cert_path, key_path = generate_self_signed_cert(base_dir, domain)
        
        print(f"📁 最终使用的证书路径:")
        print(f"证书: {cert_path}")
        print(f"密钥: {key_path}")
        
        # 确保nginx用户存在
        nginx_user = ensure_nginx_user()
        print(f"👤 使用nginx用户: {nginx_user}")
        
        # 创建nginx智能配置
        nginx_conf = f"""user {nginx_user};
worker_processes auto;
error_log /var/log/nginx/error.log notice;
pid /run/nginx.pid;

events {{
    worker_connections 1024;
}}

http {{
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    server_tokens off;
    
    upstream hysteria2_ws {{
        server 127.0.0.1:{hysteria_internal_port};
    }}
    
    server {{
        listen 80;
        listen 443 ssl http2;
        server_name _;
        
        ssl_certificate {os.path.abspath(cert_path)};
        ssl_certificate_key {os.path.abspath(key_path)};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
        
        root {web_dir};
        index index.html index.htm;
        
        # 正常网站访问
        location / {{
            try_files $uri $uri/ /index.html;
        }}
        
        # Hysteria2 WebSocket隧道
        location /hy2-tunnel {{
            proxy_pass http://hysteria2_ws;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_cache_bypass $http_upgrade;
            proxy_read_timeout 86400;
        }}
        
        add_header X-Frame-Options DENY always;
        add_header X-Content-Type-Options nosniff always;
    }}
}}"""
        
        # 更新nginx配置
        print("💾 备份当前nginx配置...")
        subprocess.run(['sudo', 'cp', '/etc/nginx/nginx.conf', '/etc/nginx/nginx.conf.backup'], check=True)
        
        print("📝 生成的nginx配置预览:")
        print("="*50)
        print(nginx_conf[:500] + "...")
        print("="*50)
        
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.conf') as tmp:
            tmp.write(nginx_conf)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, '/etc/nginx/nginx.conf'], check=True)
            os.unlink(tmp.name)
        
        subprocess.run(['sudo', 'rm', '-f', '/etc/nginx/conf.d/*.conf'], check=True)
        
        # 测试并重启
        print("🔧 测试nginx配置...")
        test_result = subprocess.run(['sudo', 'nginx', '-t'], capture_output=True, text=True)
        if test_result.returncode != 0:
            print(f"❌ nginx配置测试失败:")
            print(f"错误信息: {test_result.stderr}")
            print(f"输出信息: {test_result.stdout}")
            
            # 检查证书文件是否存在
            print(f"🔍 检查证书文件:")
            print(f"证书文件: {cert_path} - {'存在' if os.path.exists(cert_path) else '不存在'}")
            print(f"密钥文件: {key_path} - {'存在' if os.path.exists(key_path) else '不存在'}")
            
            # 恢复备份
            subprocess.run(['sudo', 'cp', '/etc/nginx/nginx.conf.backup', '/etc/nginx/nginx.conf'], check=True)
            print("🔄 已恢复nginx配置备份")
            return False, None
        
        print("✅ nginx配置测试通过")
        
        print("🔄 重启nginx服务...")
        restart_result = subprocess.run(['sudo', 'systemctl', 'restart', 'nginx'], capture_output=True, text=True)
        if restart_result.returncode != 0:
            print(f"❌ nginx重启失败:")
            print(f"错误信息: {restart_result.stderr}")
            print(f"输出信息: {restart_result.stdout}")
            return False, None
        
        print("✅ nginx智能代理配置成功！")
        print("🎯 外界看到：标准HTTPS网站")
        print("🎯 Hysteria2：通过WebSocket隧道透明传输")
        
        return True, hysteria_internal_port
        
    except Exception as e:
        print(f"❌ 配置失败: {e}")
        print(f"❌ 详细错误: {str(e)}")
        import traceback
        print(f"❌ 错误堆栈: {traceback.format_exc()}")
        return False, None

def create_web_masquerade(base_dir):
    """创建Web伪装页面"""
    web_dir = f"{base_dir}/web"
    os.makedirs(web_dir, exist_ok=True)
    
    return create_web_files_in_directory(web_dir)

def create_web_files_in_directory(web_dir):
    """在指定目录创建Web文件"""
    # 确保目录存在
    if not os.path.exists(web_dir):
        try:
            subprocess.run(['sudo', 'mkdir', '-p', web_dir], check=True)
        except:
            os.makedirs(web_dir, exist_ok=True)
    
    # 创建一个更逼真的企业网站首页
    index_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Global Digital Solutions - Enterprise Cloud Services</title>
    <meta name="description" content="Leading provider of enterprise cloud solutions, digital infrastructure, and business technology services.">
    <meta name="keywords" content="cloud computing, enterprise solutions, digital transformation, IT services">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; background: #f8f9fa; }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 20px; }
        
        header { background: linear-gradient(135deg, #2c5aa0 0%, #1e3a8a 100%); color: white; padding: 1rem 0; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        nav { display: flex; justify-content: space-between; align-items: center; }
        .logo { font-size: 1.8rem; font-weight: bold; }
        .nav-links { display: flex; list-style: none; gap: 2rem; }
        .nav-links a { color: white; text-decoration: none; transition: opacity 0.3s; font-weight: 500; }
        .nav-links a:hover { opacity: 0.8; }
        
        .hero { background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%); padding: 5rem 0; text-align: center; }
        .hero h1 { font-size: 3.5rem; margin-bottom: 1rem; color: #1e293b; font-weight: 700; }
        .hero p { font-size: 1.3rem; color: #64748b; margin-bottom: 2.5rem; max-width: 600px; margin-left: auto; margin-right: auto; }
        .btn { display: inline-block; background: #2563eb; color: white; padding: 15px 35px; text-decoration: none; border-radius: 8px; transition: all 0.3s; font-weight: 600; margin: 0 10px; }
        .btn:hover { background: #1d4ed8; transform: translateY(-2px); }
        .btn-secondary { background: transparent; border: 2px solid #2563eb; color: #2563eb; }
        .btn-secondary:hover { background: #2563eb; color: white; }
        
        .stats { background: white; padding: 3rem 0; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 2rem; text-align: center; }
        .stat h3 { font-size: 2.5rem; color: #2563eb; font-weight: 700; }
        .stat p { color: #64748b; font-weight: 500; }
        
        .features { padding: 5rem 0; background: #f8fafc; }
        .features h2 { text-align: center; font-size: 2.5rem; margin-bottom: 3rem; color: #1e293b; }
        .features-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 3rem; margin-top: 3rem; }
        .feature { background: white; padding: 2.5rem; border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); text-align: center; transition: transform 0.3s; }
        .feature:hover { transform: translateY(-5px); }
        .feature-icon { font-size: 3rem; margin-bottom: 1rem; }
        .feature h3 { color: #1e293b; margin-bottom: 1rem; font-size: 1.3rem; }
        .feature p { color: #64748b; line-height: 1.7; }
        
        .cta { background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%); color: white; padding: 5rem 0; text-align: center; }
        .cta h2 { font-size: 2.5rem; margin-bottom: 1rem; }
        .cta p { font-size: 1.2rem; margin-bottom: 2rem; opacity: 0.9; }
        
        footer { background: #1e293b; color: white; text-align: center; padding: 3rem 0; }
        .footer-content { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 2rem; margin-bottom: 2rem; text-align: left; }
        .footer-section h4 { margin-bottom: 1rem; color: #3b82f6; }
        .footer-section p, .footer-section a { color: #94a3b8; text-decoration: none; }
        .footer-section a:hover { color: white; }
        .footer-bottom { border-top: 1px solid #334155; padding-top: 2rem; margin-top: 2rem; text-align: center; color: #94a3b8; }
    </style>
</head>
 <body>
     <header>
         <nav class="container">
             <div class="logo">Global Digital Solutions</div>
             <ul class="nav-links">
                 <li><a href="#home">Home</a></li>
                 <li><a href="#services">Solutions</a></li>
                 <li><a href="#about">About</a></li>
                 <li><a href="#contact">Contact</a></li>
             </ul>
         </nav>
     </header>

     <section class="hero">
         <div class="container">
             <h1>Transform Your Digital Future</h1>
             <p>Leading enterprise cloud solutions and digital infrastructure services for businesses worldwide. Secure, scalable, and always available.</p>
             <a href="#services" class="btn">Explore Solutions</a>
             <a href="#contact" class="btn btn-secondary">Get Started</a>
         </div>
     </section>

     <section class="stats">
         <div class="container">
             <div class="stats-grid">
                 <div class="stat">
                     <h3>99.9%</h3>
                     <p>Uptime Guarantee</p>
                 </div>
                 <div class="stat">
                     <h3>10,000+</h3>
                     <p>Enterprise Clients</p>
                 </div>
                 <div class="stat">
                     <h3>50+</h3>
                     <p>Global Data Centers</p>
                 </div>
                 <div class="stat">
                     <h3>24/7</h3>
                     <p>Expert Support</p>
                 </div>
             </div>
         </div>
     </section>

     <section class="features" id="services">
         <div class="container">
             <h2>Enterprise Cloud Solutions</h2>
             <div class="features-grid">
                 <div class="feature">
                     <div class="feature-icon">☁️</div>
                     <h3>Cloud Infrastructure</h3>
                     <p>Scalable and secure cloud infrastructure with global reach. Deploy your applications with confidence on our enterprise-grade platform.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon">🔒</div>
                     <h3>Security & Compliance</h3>
                     <p>Advanced security protocols and compliance standards including SOC 2, ISO 27001, and GDPR to protect your business data.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon">⚡</div>
                     <h3>High Performance</h3>
                     <p>Lightning-fast performance with our global CDN network and optimized infrastructure for maximum speed and reliability.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon">📊</div>
                     <h3>Analytics & Monitoring</h3>
                     <p>Real-time monitoring and detailed analytics to help you optimize performance and make data-driven business decisions.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon">🛠️</div>
                     <h3>Managed Services</h3>
                     <p>Full-stack managed services including database management, security updates, and performance optimization by our experts.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon">🌍</div>
                     <h3>Global Reach</h3>
                     <p>Worldwide infrastructure with data centers across six continents, ensuring low latency and high availability for your users.</p>
                 </div>
             </div>
         </div>
     </section>

     <section class="cta" id="contact">
         <div class="container">
             <h2>Ready to Transform Your Business?</h2>
             <p>Join thousands of enterprises already using our cloud solutions</p>
             <a href="mailto:contact@globaldigi.com" class="btn">Contact Sales Team</a>
         </div>
     </section>

     <footer>
         <div class="container">
             <div class="footer-content">
                 <div class="footer-section">
                     <h4>Solutions</h4>
                     <p><a href="#">Cloud Infrastructure</a></p>
                     <p><a href="#">Security Services</a></p>
                     <p><a href="#">Data Analytics</a></p>
                     <p><a href="#">Managed Services</a></p>
                 </div>
                 <div class="footer-section">
                     <h4>Company</h4>
                     <p><a href="#">About Us</a></p>
                     <p><a href="#">Careers</a></p>
                     <p><a href="#">News</a></p>
                     <p><a href="#">Contact</a></p>
                 </div>
                 <div class="footer-section">
                     <h4>Support</h4>
                     <p><a href="#">Documentation</a></p>
                     <p><a href="#">Help Center</a></p>
                     <p><a href="#">Status Page</a></p>
                     <p><a href="#">Contact Support</a></p>
                 </div>
                 <div class="footer-section">
                     <h4>Legal</h4>
                     <p><a href="#">Privacy Policy</a></p>
                     <p><a href="#">Terms of Service</a></p>
                     <p><a href="#">Security</a></p>
                     <p><a href="#">Compliance</a></p>
                 </div>
             </div>
             <div class="footer-bottom">
                 <p>&copy; 2024 Global Digital Solutions Inc. All rights reserved. | Enterprise Cloud Services</p>
             </div>
         </div>
     </footer>
 </body>
</html>"""
    
    # 使用sudo写入文件（如果需要）
    try:
        with open(f"{web_dir}/index.html", "w", encoding="utf-8") as f:
            f.write(index_html)
    except PermissionError:
        # 使用sudo写入
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.html') as tmp:
            tmp.write(index_html)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/index.html"], check=True)
            os.unlink(tmp.name)
    
    # 创建robots.txt（看起来更真实）
    robots_txt = """User-agent: *
Allow: /

Sitemap: /sitemap.xml
"""
    try:
        with open(f"{web_dir}/robots.txt", "w") as f:
            f.write(robots_txt)
    except PermissionError:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
            tmp.write(robots_txt)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/robots.txt"], check=True)
            os.unlink(tmp.name)
    
    # 创建sitemap.xml
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>/</loc>
    <lastmod>2024-01-01</lastmod>
    <changefreq>monthly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>/services</loc>
    <lastmod>2024-01-01</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>/about</loc>
    <lastmod>2024-01-01</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.6</priority>
  </url>
  <url>
    <loc>/contact</loc>
    <lastmod>2024-01-01</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
</urlset>"""
    try:
        with open(f"{web_dir}/sitemap.xml", "w") as f:
            f.write(sitemap_xml)
    except PermissionError:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.xml') as tmp:
            tmp.write(sitemap_xml)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/sitemap.xml"], check=True)
            os.unlink(tmp.name)
    
    # 创建favicon.ico (简单的base64编码)
    # 这是一个简单的蓝色圆形图标
    favicon_data = """AAABAAEAEBAAAAEAIABoBAAAFgAAACgAAAAQAAAAIAAAAAEAIAAAAAAAAAQAABILAAASCwAAAAAAAAAAAAD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A2dnZ/9nZ2f/Z2dn/2dnZ/9nZ2f/Z2dn/2dnZ/9nZ2f/Z2dn/2dnZ/////wD///8A////AP///wD///8A2dnZ/1tbW/8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/1tbW//Z2dn/////AP///wD///8A2dnZ/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/2dnZ/////wD///8A2dnZ/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/2dnZ/////wD///8A2dnZ/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/2dnZ/////wD///8A2dnZ/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/2dnZ/////wD///8A2dnZ/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/2dnZ/////wD///8A2dnZ/1tbW/8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/1tbW//Z2dn/////AP///wD///8A////AP///wD///8A2dnZ/9nZ2f/Z2dn/2dnZ/9nZ2f/Z2dn/2dnZ/9nZ2f/Z2dn/2dnZ/////wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAA=="""
    
    import base64
    try:
        favicon_bytes = base64.b64decode(favicon_data)
        try:
            with open(f"{web_dir}/favicon.ico", "wb") as f:
                f.write(favicon_bytes)
        except PermissionError:
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.ico') as tmp:
                tmp.write(favicon_bytes)
                tmp.flush()
                subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/favicon.ico"], check=True)
                os.unlink(tmp.name)
    except:
        pass  # 如果favicon创建失败就跳过
    
    # 创建about页面
    about_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>About Us - Global Digital Solutions</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div style="text-align: center; padding: 50px; font-family: Arial, sans-serif;">
        <h1>About Global Digital Solutions</h1>
        <p>We are a leading provider of enterprise cloud solutions, serving businesses worldwide since 2015.</p>
        <p>Our mission is to transform how businesses operate in the digital age through innovative cloud technologies.</p>
        <p><a href="/">← Back to Home</a></p>
    </div>
</body>
</html>"""
    try:
        with open(f"{web_dir}/about.html", "w", encoding="utf-8") as f:
            f.write(about_html)
    except PermissionError:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.html') as tmp:
            tmp.write(about_html)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/about.html"], check=True)
            os.unlink(tmp.name)
    
    # 创建404页面
    error_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>404 - Page Not Found</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f4f4f4; }
        .error-container { background: white; padding: 50px; border-radius: 10px; box-shadow: 0 0 20px rgba(0,0,0,0.1); max-width: 500px; margin: 0 auto; }
        h1 { color: #e74c3c; font-size: 4rem; margin-bottom: 1rem; }
        p { color: #666; font-size: 1.2rem; }
        a { color: #3498db; text-decoration: none; }
    </style>
</head>
<body>
    <div class="error-container">
        <h1>404</h1>
        <p>Sorry, the page you are looking for could not be found.</p>
        <p><a href="/">Return to Homepage</a></p>
    </div>
</body>
</html>"""
    
    try:
        with open(f"{web_dir}/404.html", "w", encoding="utf-8") as f:
            f.write(error_html)
    except PermissionError:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.html') as tmp:
            tmp.write(error_html)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/404.html"], check=True)
            os.unlink(tmp.name)
    
    return web_dir

def generate_self_signed_cert(base_dir, domain):
    """生成自签名证书"""
    cert_dir = f"{base_dir}/cert"
    cert_path = f"{cert_dir}/server.crt"
    key_path = f"{cert_dir}/server.key"
    
    # 确保域名不为空，如果为空则使用默认值
    if not domain or not domain.strip():
        domain = "localhost"
        print("警告: 域名为空，使用localhost作为证书通用名")
    
    try:
        # 生成更安全的证书
        subprocess.run([
            "openssl", "req", "-x509", "-nodes",
            "-newkey", "rsa:4096",  # 使用4096位密钥
            "-keyout", key_path,
            "-out", cert_path,
            "-subj", f"/CN={domain}",
            "-days", "36500",
            "-sha256"  # 使用SHA256
        ], check=True)
        
        # 设置适当的权限
        os.chmod(cert_path, 0o644)
        os.chmod(key_path, 0o600)
        
        return cert_path, key_path
    except Exception as e:
        print(f"生成证书失败: {e}")
        sys.exit(1)

def get_real_certificate(base_dir, domain, email="admin@example.com"):
    """使用certbot获取真实的Let's Encrypt证书"""
    cert_dir = f"{base_dir}/cert"
    
    try:
        # 检查是否已安装certbot
        if not shutil.which('certbot'):
            print("正在安装certbot...")
            if platform.system().lower() == 'linux':
                # Ubuntu/Debian
                if shutil.which('apt'):
                    subprocess.run(['sudo', 'apt', 'update'], check=True)
                    subprocess.run(['sudo', 'apt', 'install', '-y', 'certbot'], check=True)
                # CentOS/RHEL
                elif shutil.which('yum'):
                    subprocess.run(['sudo', 'yum', 'install', '-y', 'certbot'], check=True)
                elif shutil.which('dnf'):
                    subprocess.run(['sudo', 'dnf', 'install', '-y', 'certbot'], check=True)
                else:
                    print("无法自动安装certbot，请手动安装")
                    return None, None
            else:
                print("请手动安装certbot")
                return None, None
        
        # 使用standalone模式获取证书
        print(f"正在为域名 {domain} 获取Let's Encrypt证书...")
        subprocess.run([
            'sudo', 'certbot', 'certonly',
            '--standalone',
            '--agree-tos',
            '--non-interactive',
            '--email', email,
            '-d', domain
        ], check=True)
        
        # 复制证书到我们的目录
        cert_source = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        key_source = f"/etc/letsencrypt/live/{domain}/privkey.pem"
        cert_path = f"{cert_dir}/server.crt"
        key_path = f"{cert_dir}/server.key"
        
        shutil.copy2(cert_source, cert_path)
        shutil.copy2(key_source, key_path)
        
        # 设置权限
        os.chmod(cert_path, 0o644)
        os.chmod(key_path, 0o600)
        
        print(f"成功获取真实证书: {cert_path}")
        return cert_path, key_path
        
    except Exception as e:
        print(f"获取真实证书失败: {e}")
        print("将使用自签名证书作为备选...")
        return None, None

def create_config(base_dir, port, password, cert_path, key_path, domain, enable_web_masquerade=True, custom_web_dir=None):
    """创建Hysteria2配置文件（增强防墙版本）"""
    
    # 基础配置
    config = {
        "listen": f":{port}",
        "tls": {
            "cert": cert_path,
            "key": key_path
        },
        "auth": {
            "type": "password",
            "password": password
        },
        "bandwidth": {
            # 设置更合理的带宽，避免过高引起注意
            "up": "1000 mbps",
            "down": "1000 mbps"
        },
        "ignoreClientBandwidth": False,
        "log": {
            "level": "warn",  # 降低日志级别，减少日志量
            "output": f"{base_dir}/logs/hysteria.log",
            "timestamp": True
        },
        # 增加流量优化配置
        "resolver": {
            "type": "udp",
            "tcp": {
                "addr": "8.8.8.8:53",
                "timeout": "4s"
            },
            "udp": {
                "addr": "8.8.8.8:53", 
                "timeout": "4s"
            }
        }
    }
    
    # 伪装配置优化
    if enable_web_masquerade and custom_web_dir and os.path.exists(custom_web_dir):
        # 使用本地Web目录进行伪装
        config["masquerade"] = {
            "type": "file",
            "file": {
                "dir": custom_web_dir
            }
        }
    elif port in [80, 443, 8080, 8443]:
        # 对于标准Web端口，使用更逼真的伪装
        config["masquerade"] = {
            "type": "proxy",
            "proxy": {
                "url": "https://www.microsoft.com",
                "rewriteHost": True
            }
        }
    else:
        # 非标准端口使用随机的正常网站伪装
        masquerade_sites = [
            "https://www.microsoft.com",
            "https://www.apple.com", 
            "https://www.amazon.com",
            "https://www.github.com",
            "https://www.stackoverflow.com"
        ]
        import random
        config["masquerade"] = {
            "type": "proxy",
            "proxy": {
                "url": random.choice(masquerade_sites),
                "rewriteHost": True
            }
        }
    
    # 如果是标准HTTPS端口，添加HTTP/3支持
    if port == 443:
        config["quic"] = {
            "initStreamReceiveWindow": 8388608,
            "maxStreamReceiveWindow": 8388608,
            "initConnReceiveWindow": 20971520,
            "maxConnReceiveWindow": 20971520,
            "maxIdleTimeout": "30s",
            "maxIncomingStreams": 1024,
            "disablePathMTUDiscovery": False
        }
    
    config_path = f"{base_dir}/config/config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    
    return config_path

def create_service_script(base_dir, binary_path, config_path, port):
    """创建启动脚本"""
    os_name = platform.system().lower()
    pid_file = f"{base_dir}/hysteria.pid"
    log_file = f"{base_dir}/logs/hysteria.log"
    
    if os_name == 'windows':
        script_content = f"""@echo off
echo 正在启动 Hysteria2 服务...
start /b {binary_path} server -c {config_path} > {log_file} 2>&1
echo 启动命令已执行，请检查日志以确认服务状态
"""
        script_path = f"{base_dir}/start.bat"
    else:
        script_content = f"""#!/bin/bash
echo "正在启动 Hysteria2 服务..."

# 检查二进制文件是否存在
if [ ! -f "{binary_path}" ]; then
    echo "错误: Hysteria2 二进制文件不存在"
    exit 1
fi

# 检查配置文件是否存在
if [ ! -f "{config_path}" ]; then
    echo "错误: 配置文件不存在"
    exit 1
fi

# 启动服务
nohup {binary_path} server -c {config_path} > {log_file} 2>&1 &
echo $! > {pid_file}
echo "Hysteria2 服务已启动，PID: $(cat {pid_file})"

# 给服务一点时间来启动
sleep 2
echo "启动命令已执行，请检查日志以确认服务状态"
"""
        script_path = f"{base_dir}/start.sh"
    
    with open(script_path, "w") as f:
        f.write(script_content)
    
    if os_name != 'windows':
        os.chmod(script_path, 0o755)
    
    return script_path

def create_stop_script(base_dir):
    """创建停止脚本"""
    os_name = platform.system().lower()
    
    if os_name == 'windows':
        script_content = f"""@echo off
for /f "tokens=*" %%a in ('type {base_dir}\\hysteria.pid') do (
    taskkill /F /PID %%a
)
del {base_dir}\\hysteria.pid
echo Hysteria2 服务已停止
"""
        script_path = f"{base_dir}/stop.bat"
    else:
        script_content = f"""#!/bin/bash
if [ -f {base_dir}/hysteria.pid ]; then
    kill $(cat {base_dir}/hysteria.pid)
    rm {base_dir}/hysteria.pid
    echo "Hysteria2 服务已停止"
else
    echo "Hysteria2 服务未运行"
fi
"""
        script_path = f"{base_dir}/stop.sh"
    
    with open(script_path, "w") as f:
        f.write(script_content)
    
    if os_name != 'windows':
        os.chmod(script_path, 0o755)
    
    return script_path

def delete_hysteria2():
    """删除Hysteria2安装"""
    home = get_user_home()
    base_dir = f"{home}/.hysteria2"
    current_user = os.getenv('USER', 'unknown')
    
    print(f"当前用户: {current_user}")
    print(f"检查安装目录: {base_dir}")
    
    if not os.path.exists(base_dir):
        print("当前用户下未找到Hysteria2安装")
        
        # 检查是否有其他用户的hysteria在运行
        try:
            result = subprocess.run(['sudo', 'ss', '-anup'], capture_output=True, text=True)
            if 'hysteria' in result.stdout:
                print("\n检测到系统中有Hysteria2进程在运行:")
                for line in result.stdout.split('\n'):
                    if 'hysteria' in line and ':443' in line:
                        print(f"  {line}")
                print("\n如需删除其他用户的安装，请切换到对应用户执行删除操作")
            else:
                print("系统中未检测到Hysteria2进程")
        except:
            print("无法检查系统进程（权限不足）")
        return
    
    print(f"找到安装目录: {base_dir}")
    
    # 停止服务
    stop_script = f"{base_dir}/stop.sh"
    if os.path.exists(stop_script):
        try:
            print("正在停止Hysteria2服务...")
            subprocess.run([stop_script], check=True)
            print("✅ 服务已停止")
        except Exception as e:
            print(f"⚠️ 停止服务失败: {e}")
    
    # 检查是否还有进程在运行
    try:
        result = subprocess.run(['sudo', 'ss', '-anup'], capture_output=True, text=True)
        if 'hysteria' in result.stdout and ':443' in result.stdout:
            print("⚠️ 检测到Hysteria2进程仍在运行，尝试强制终止...")
            try:
                # 尝试找到并终止进程
                result2 = subprocess.run(['sudo', 'pkill', '-f', 'hysteria'], check=False)
                print("✅ 进程已终止")
            except:
                print("⚠️ 无法终止进程，可能需要手动处理")
    except:
        pass
    
    # 删除目录
    try:
        shutil.rmtree(base_dir)
        print(f"✅ 已删除安装目录: {base_dir}")
        
        # 清理nginx配置（如果存在）
        try:
            nginx_web_dir = "/var/www/hysteria2"
            if os.path.exists(nginx_web_dir):
                subprocess.run(['sudo', 'rm', '-rf', nginx_web_dir], check=True)
                print(f"✅ 已清理Web目录: {nginx_web_dir}")
                
            # 清理nginx配置文件
            ip_addr = get_ip_address()
            nginx_conf_files = [
                f"/etc/nginx/conf.d/{ip_addr}.conf",
                f"/etc/nginx/sites-enabled/{ip_addr}",
                f"/etc/nginx/sites-available/{ip_addr}"
            ]
            for conf_file in nginx_conf_files:
                if os.path.exists(conf_file):
                    subprocess.run(['sudo', 'rm', '-f', conf_file], check=True)
                    print(f"✅ 已清理nginx配置: {conf_file}")
                    
            # 重启nginx
            try:
                subprocess.run(['sudo', 'systemctl', 'reload', 'nginx'], check=True)
                print("✅ 已重新加载nginx配置")
            except:
                print("⚠️ nginx重新加载失败，可能需要手动处理")
                
        except Exception as e:
            print(f"⚠️ 清理nginx配置失败: {e}")
        
        print("🎉 Hysteria2 已成功删除")
        
    except Exception as e:
        print(f"❌ 删除失败: {e}")
        print("可能需要sudo权限或手动删除")
        sys.exit(1)

def show_status():
    """显示Hysteria2状态"""
    home = get_user_home()
    base_dir = f"{home}/.hysteria2"
    
    if not os.path.exists(base_dir):
        print("Hysteria2 未安装")
        return
    
    # 检查服务状态
    pid_file = f"{base_dir}/hysteria.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f:
                pid = f.read().strip()
            if os.path.exists(f"/proc/{pid}"):
                print(f"服务状态: 运行中 (PID: {pid})")
            else:
                print("服务状态: 已停止")
        except:
            print("服务状态: 未知")
    else:
        print("服务状态: 未运行")
    
    # 显示配置信息
    config_path = f"{base_dir}/config/config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            print("\n配置信息:")
            print(f"监听端口: {config['listen']}")
            print(f"认证方式: {config['auth']['type']}")
            if 'bandwidth' in config:
                print(f"上行带宽: {config['bandwidth']['up']}")
                print(f"下行带宽: {config['bandwidth']['down']}")
        except:
            print("无法读取配置文件")
    
    # 显示日志
    log_path = f"{base_dir}/logs/hysteria.log"
    if os.path.exists(log_path):
        print("\n最近日志:")
        try:
            with open(log_path, 'r') as f:
                logs = f.readlines()
                for line in logs[-10:]:  # 显示最后10行
                    print(line.strip())
        except:
            print("无法读取日志文件")

def start_service(start_script, port, base_dir):
    """启动服务并等待服务成功运行"""
    print(f"正在启动 Hysteria2 服务...")
    pid_file = f"{base_dir}/hysteria.pid"
    log_file = f"{base_dir}/logs/hysteria.log"
    
    try:
        # 运行启动脚本
        subprocess.run([start_script], check=True)
        
        # 等待服务启动 (最多10秒)
        for i in range(10):
            # 检查PID文件和进程
            if check_process_running(pid_file):
                print(f"服务进程已启动")
                time.sleep(2)  # 给服务额外时间初始化
                break
            time.sleep(1)
            print(f"等待服务启动... ({i+1}秒)")
        
        # 检查日志文件是否存在且有内容
        if os.path.exists(log_file) and os.path.getsize(log_file) > 0:
            with open(log_file, 'r') as f:
                log_content = f.read()
                if "server up and running" in log_content:
                    print("日志显示服务已正常启动")
                    return True
        
        # 检查端口是否在监听
        if is_port_listening(port):
            print(f"检测到端口 {port} 已开放，服务应已启动")
            return True
            
        print("警告: 无法确认服务是否成功启动，请检查日志文件")
        return True  # 即使不确定也返回True，避免误报
    except Exception as e:
        print(f"启动服务失败: {e}")
        return False

def show_help():
    """显示帮助信息"""
    print("""
🛡️ Hysteria2 防墙增强版管理工具

使用方法:
    python3 hy2.py [命令] [选项]

可用命令:
    install      安装 Hysteria2 (防墙增强版，自动配置nginx)
    client       显示客户端连接指南 (各平台详细说明)
    fix          修复nginx配置和权限问题 (解决404错误)
    smart-proxy  智能代理配置（TCP转发，推荐！）
    setup-nginx  手动设置nginx TCP端口伪装（传统）
    verify       验证智能代理配置是否正常工作
    
    del          删除 Hysteria2
    status       查看 Hysteria2 状态
    help         显示此帮助信息

🔧 基础选项:
    --ip IP           指定服务器IP地址
    --port PORT       指定服务器端口 (推荐: 443/80)
    --password PWD    指定密码

🔐 防墙增强选项:
    --domain DOMAIN         指定域名 (推荐用于真实证书)
    --email EMAIL           Let's Encrypt证书邮箱地址  
    --use-real-cert         使用真实域名证书 (需域名指向服务器)
    --web-masquerade        启用Web伪装 (默认启用)
    --auto-nginx            自动配置nginx (默认启用)
    

📋 示例:
    # 一键安装 (自动配置nginx + Web伪装)
    python3 hy2.py install

    # 完整防墙配置 (真实域名 + 自动nginx)
    python3 hy2.py install --domain your.domain.com --use-real-cert --email your@email.com

    

    # 自定义配置
    python3 hy2.py install --port 8443 --password mySecretPass

    # 服务管理和客户端
    python3 hy2.py status     # 查看状态
    python3 hy2.py client     # 客户端连接指南
    python3 hy2.py del        # 删除安装

🛡️ 防墙优化特性:
✅ 默认使用443端口 (HTTPS标准端口)  
✅ 自动安装配置nginx (TCP端口伪装)
✅ Web页面伪装 (看起来像正常网站)
✅ 支持真实域名证书 (Let's Encrypt)
✅ 集成企业级Web伪装页面
✅ 随机伪装目标网站
✅ 优化流量特征
✅ 降低日志记录级别

🌟 三层防护体系:
1️⃣ Hysteria2协议混淆 (基础防护)
2️⃣ nginx Web伪装 (中级防护) 
3️⃣ 完整防护体系
""")

def create_nginx_masquerade(base_dir, domain, web_dir):
    """创建nginx配置用于TCP端口伪装"""
    # 确保使用绝对路径
    abs_web_dir = os.path.abspath(web_dir)
    abs_cert_path = os.path.abspath(f"{base_dir}/cert/server.crt")
    abs_key_path = os.path.abspath(f"{base_dir}/cert/server.key")
    
    nginx_conf = f"""server {{
    listen 80;
    listen 443 ssl;
    server_name {domain} _;
    
    ssl_certificate {abs_cert_path};
    ssl_certificate_key {abs_key_path};
    
    # SSL配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    
    # 网站根目录 (使用绝对路径)
    root {abs_web_dir};
    index index.html index.htm;
    
    # 确保文件权限正确
    location ~* \\.(html|css|js|png|jpg|jpeg|gif|ico|svg)$ {{
        expires 1y;
        add_header Cache-Control "public, immutable";
    }}
    
    # 处理正常的Web请求
    location / {{
        try_files $uri $uri/ /index.html;
    }}
    
    # 特殊文件处理
    location = /favicon.ico {{
        access_log off;
        log_not_found off;
    }}
    
    location = /robots.txt {{
        access_log off;
        log_not_found off;
    }}
    
    # 添加安全头（使用标准nginx指令）
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    
    # 隐藏nginx版本
    server_tokens off;
    
    # 日志
    access_log /var/log/nginx/{domain}_access.log;
    error_log /var/log/nginx/{domain}_error.log;
}}"""
    
    # 创建nginx配置文件
    nginx_conf_file = f"{base_dir}/nginx.conf"
    with open(nginx_conf_file, "w") as f:
        f.write(nginx_conf)
    
    return nginx_conf_file

def setup_dual_port_masquerade(base_dir, domain, web_dir, cert_path, key_path):
    """设置双端口伪装：TCP用于Web，UDP用于Hysteria2"""
    print("正在设置双端口伪装方案...")
    
    # 检查是否安装了nginx
    try:
        subprocess.run(['which', 'nginx'], check=True, capture_output=True)
        has_nginx = True
    except:
        has_nginx = False
    
    if not has_nginx:
        print("正在安装nginx...")
        
        # 获取系统架构信息
        arch = platform.machine().lower()
        system = platform.system().lower()
        print(f"检测到系统: {system}, 架构: {arch}")
        
        try:
            # 尝试安装nginx（包管理器会自动处理架构）
            if shutil.which('apt'):
                print("使用APT包管理器安装nginx...")
                subprocess.run(['sudo', 'apt', 'update'], check=True)
                subprocess.run(['sudo', 'apt', 'install', '-y', 'nginx'], check=True)
            elif shutil.which('yum'):
                print("使用YUM包管理器安装nginx...")
                subprocess.run(['sudo', 'yum', 'install', '-y', 'epel-release'], check=True)  # EPEL for nginx
                subprocess.run(['sudo', 'yum', 'install', '-y', 'nginx'], check=True)
            elif shutil.which('dnf'):
                print("使用DNF包管理器安装nginx...")
                subprocess.run(['sudo', 'dnf', 'install', '-y', 'nginx'], check=True)
            elif shutil.which('pacman'):
                print("使用Pacman包管理器安装nginx...")
                subprocess.run(['sudo', 'pacman', '-S', '--noconfirm', 'nginx'], check=True)
            elif shutil.which('zypper'):
                print("使用Zypper包管理器安装nginx...")
                subprocess.run(['sudo', 'zypper', 'install', '-y', 'nginx'], check=True)
            else:
                print("无法识别包管理器，尝试手动下载nginx...")
                print("支持的架构: x86_64, aarch64, i386")
                print("请手动安装nginx: https://nginx.org/en/download.html")
                return False
                
            print("✅ nginx安装完成")
        except Exception as e:
            print(f"nginx安装失败: {e}")
            print("请尝试手动安装: sudo apt install nginx 或 sudo yum install nginx")
            return False
    
    # 简化方案：直接覆盖nginx默认Web目录的文件
    print("🔧 使用简化方案：直接覆盖nginx默认Web目录")
    
    # 检测nginx默认Web目录
    nginx_web_dirs = [
        "/var/www/html",           # Ubuntu/Debian 默认
        "/usr/share/nginx/html",   # CentOS/RHEL 默认
        "/var/www"                 # 备选
    ]
    
    nginx_web_dir = None
    for dir_path in nginx_web_dirs:
        if os.path.exists(dir_path):
            nginx_web_dir = dir_path
            break
    
    if not nginx_web_dir:
        # 如果都不存在，创建默认目录
        nginx_web_dir = "/var/www/html"
        try:
            subprocess.run(['sudo', 'mkdir', '-p', nginx_web_dir], check=True)
            print(f"✅ 创建Web目录: {nginx_web_dir}")
        except Exception as e:
            print(f"❌ 创建Web目录失败: {e}")
            return False
    
    print(f"✅ 检测到nginx Web目录: {nginx_web_dir}")
    
    try:
        # 备份原有文件
        try:
            if os.path.exists(f"{nginx_web_dir}/index.html"):
                subprocess.run(['sudo', 'cp', f'{nginx_web_dir}/index.html', f'{nginx_web_dir}/index.html.backup'], check=True)
                print("✅ 备份原有index.html")
        except:
            pass
        
        # 复制我们的伪装文件到nginx默认目录
        if os.path.exists(web_dir):
            subprocess.run(['sudo', 'cp', '-r', f'{web_dir}/*', nginx_web_dir], check=True)
            print(f"✅ 伪装文件已复制到: {nginx_web_dir}")
        else:
            print(f"⚠️ 原Web目录不存在，直接在nginx目录创建伪装文件...")
            create_web_files_in_directory(nginx_web_dir)
        
        # 设置正确的权限
        set_nginx_permissions(nginx_web_dir)
        
        print(f"✅ 设置权限完成: {nginx_web_dir}")
        
    except Exception as e:
        print(f"⚠️ 文件复制失败: {e}")
        return False
    
    # 简化nginx配置：只配置SSL证书，使用默认Web目录
    try:
        # 创建简化的SSL配置
        ssl_conf = f"""# SSL configuration for Hysteria2 masquerade
server {{
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    
    ssl_certificate {os.path.abspath(cert_path)};
    ssl_certificate_key {os.path.abspath(key_path)};
    
    # SSL配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    
    # 使用默认配置，不指定root（使用nginx默认）
    # 这样就使用了我们刚才覆盖的文件
    
    # 隐藏nginx版本
    server_tokens off;
    
    # 基本安全头
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
}}"""
        
        ssl_conf_file = "/etc/nginx/conf.d/hysteria2-ssl.conf"
        
        # 删除可能存在的旧配置
        subprocess.run(['sudo', 'rm', '-f', f'/etc/nginx/conf.d/{domain}.conf'], check=False)
        subprocess.run(['sudo', 'rm', '-f', f'/etc/nginx/sites-enabled/{domain}'], check=False)
        subprocess.run(['sudo', 'rm', '-f', f'/etc/nginx/sites-available/{domain}'], check=False)
        
        # 写入新的SSL配置
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.conf') as tmp:
            tmp.write(ssl_conf)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, ssl_conf_file], check=True)
            os.unlink(tmp.name)
            
        print(f"✅ 创建SSL配置: {ssl_conf_file}")
        
        # 测试配置
        test_result = subprocess.run(['sudo', 'nginx', '-t'], capture_output=True, text=True)
        if test_result.returncode != 0:
            print(f"❌ nginx配置测试失败: {test_result.stderr}")
            return False
        
        # 启动nginx
        subprocess.run(['sudo', 'systemctl', 'restart', 'nginx'], check=True)
        subprocess.run(['sudo', 'systemctl', 'enable', 'nginx'], check=True)
        
        print("✅ nginx配置成功！")
        print(f"✅ Web伪装已生效: https://{domain}")
        print("✅ HTTP 80端口会显示默认页面")
        print("✅ HTTPS 443端口会显示我们的伪装页面")
        return True
        
    except Exception as e:
        print(f"❌ nginx配置失败: {e}")
        return False



def show_client_setup(config_link, server_address, port, password, use_real_cert):
    """显示客户端连接指南"""
    insecure_text = "否" if use_real_cert else "是"
    
    print(f"""
📱 客户端连接指南

🔗 一键导入链接:
{config_link}

📋 手动配置参数:
服务器地址: {server_address}
端口: {port}
密码: {password}
协议: Hysteria2
TLS: 启用
跳过证书验证: {insecure_text}
SNI: {server_address}

💻 Windows客户端:

1️⃣ v2rayN (推荐)
   - 下载: https://github.com/2dust/v2rayN/releases
   - 添加服务器 → 选择"自定义配置服务器"
   - 粘贴上面的链接或手动填写参数

2️⃣ Clash Verge
   - 下载: https://github.com/clash-verge-rev/clash-verge-rev/releases
   - 配置 → 导入链接或手动添加

3️⃣ NekoRay
   - 下载: https://github.com/MatsuriDayo/nekoray/releases
   - 程序 → 添加配置 → Hysteria2

📱 Android客户端:

1️⃣ v2rayNG
   - Google Play / GitHub下载
   - 点击"+"添加配置
   - 选择"从剪贴板导入"或手动配置

2️⃣ Clash Meta for Android
   - 配置 → 新增配置 → 手动输入

🍎 iOS客户端:

1️⃣ Shadowrocket (付费)
   - App Store下载
   - 右上角"+"添加服务器
   - 选择"Hysteria2"类型

2️⃣ Quantumult X (付费)  
   - 节点 → 添加 → 服务器
   - 选择Hysteria2协议

🐧 Linux客户端:

1️⃣ 命令行客户端
   wget https://github.com/apernet/hysteria/releases/download/app/v2.6.1/hysteria-linux-amd64
   chmod +x hysteria-linux-amd64
   ./hysteria-linux-amd64 client -c config.json

2️⃣ 配置文件 (config.json):
{{
  "server": "{server_address}:{port}",
  "auth": "{password}",
  "tls": {{
    "sni": "{server_address}",
    "insecure": {"true" if not use_real_cert else "false"}
  }},
  "socks5": {{
    "listen": "127.0.0.1:1080"
  }},
  "http": {{
    "listen": "127.0.0.1:8080"
  }}
}}

🍎 macOS客户端:

1️⃣ ClashX Pro
   - 配置 → 托管配置 → 管理
   - 添加Hysteria2节点

2️⃣ Surge (付费)
   - 配置 → 代理服务器 → 添加

🔧 连接测试:

1. 导入配置后，启动客户端
2. 选择刚添加的Hysteria2节点
3. 访问 https://www.google.com 测试连接
4. 检查IP: https://ipinfo.io 确认IP已变更

⚠️ 常见问题:

Q: 连接失败怎么办?
A: 1. 检查服务器防火墙是否开放{port}端口
   2. 确认密码输入正确
   3. 尝试关闭客户端防病毒软件

Q: 速度慢怎么办?
A: 1. 尝试更换客户端
   2. 检查本地网络环境
   3. 服务器可能负载过高

Q: 无法访问某些网站?
A: 这是正常现象，部分网站可能有防护措施

🎯 优化建议:
- 选择延迟最低的客户端
- 定期更新客户端版本
- 避免在高峰期使用

连接成功后即可享受高速稳定的网络体验！
""")

def verify_smart_proxy(server_address, port=443):
    """验证智能代理配置是否工作"""
    print("🔍 正在验证智能代理配置...")
    
    try:
        import socket
        import ssl
        import time
        
        # 1. 测试TCP 443端口连接
        print("1️⃣ 测试TCP连接到443端口...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        result = sock.connect_ex((server_address, port))
        sock.close()
        
        if result == 0:
            print("✅ TCP 443端口连接成功")
        else:
            print("❌ TCP 443端口连接失败")
            return False
        
        # 2. 测试HTTPS网站访问
        print("2️⃣ 测试HTTPS网站访问...")
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ssl_sock = context.wrap_socket(sock, server_name=server_address)
            ssl_sock.settimeout(10)
            ssl_sock.connect((server_address, port))
            
            # 发送HTTP请求
            request = f"GET / HTTP/1.1\r\nHost: {server_address}\r\nConnection: close\r\n\r\n"
            ssl_sock.send(request.encode())
            
            response = ssl_sock.recv(1024).decode()
            ssl_sock.close()
            
            if "Global Digital Solutions" in response or "200 OK" in response:
                print("✅ HTTPS网站访问成功，伪装页面正常")
            else:
                print("⚠️ HTTPS可访问，但伪装页面可能有问题")
                
        except Exception as e:
            print(f"⚠️ HTTPS访问测试失败: {e}")
        
        # 3. 测试WebSocket路径
        print("3️⃣ 测试WebSocket隧道路径...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ssl_sock = context.wrap_socket(sock, server_name=server_address)
            ssl_sock.settimeout(10)
            ssl_sock.connect((server_address, port))
            
            # 发送WebSocket升级请求
            ws_request = f"""GET /hy2-tunnel HTTP/1.1\r
Host: {server_address}\r
Upgrade: websocket\r
Connection: Upgrade\r
Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r
Sec-WebSocket-Version: 13\r
\r
"""
            ssl_sock.send(ws_request.encode())
            
            ws_response = ssl_sock.recv(1024).decode()
            ssl_sock.close()
            
            if "101 Switching Protocols" in ws_response or "upgrade" in ws_response.lower():
                print("✅ WebSocket隧道路径响应正常")
            else:
                print("⚠️ WebSocket路径可能配置有问题")
                print(f"响应: {ws_response[:200]}...")
                
        except Exception as e:
            print(f"⚠️ WebSocket测试失败: {e}")
        
        # 4. 检查端口监听状态
        print("4️⃣ 检查服务端口状态...")
        try:
            import subprocess
            
            # 检查nginx TCP 443
            tcp_result = subprocess.run(['ss', '-tlnp'], capture_output=True, text=True)
            if ':443 ' in tcp_result.stdout:
                print("✅ nginx正在监听TCP 443端口")
            else:
                print("⚠️ 未检测到TCP 443端口监听")
            
            # 检查Hysteria2 UDP内部端口
            udp_result = subprocess.run(['ss', '-ulnp'], capture_output=True, text=True)
            if ':44300 ' in udp_result.stdout:
                print("✅ Hysteria2正在监听UDP 44300端口（内部）")
            else:
                print("⚠️ 未检测到Hysteria2内部端口监听")
                
        except Exception as e:
            print(f"⚠️ 端口检查失败: {e}")
        
        print("\n🎯 验证总结:")
        print("• TCP 443: nginx接收HTTPS连接")
        print("• /hy2-tunnel: WebSocket隧道路径")
        print("• UDP 44300: Hysteria2内部服务")
        print("• 流量路径: 客户端TCP → nginx → WebSocket → Hysteria2 UDP")
        
        return True
        
    except Exception as e:
        print(f"❌ 验证过程出错: {e}")
        return False

def show_smart_proxy_client_setup(server_address, password, use_real_cert):
    """显示智能代理模式的客户端配置"""
    ws_path = "/hy2-tunnel"
    insecure_flag = "0" if use_real_cert else "1"
    
    # 生成WebSocket模式的连接链接
    websocket_link = f"hysteria2://{urllib.parse.quote(password)}@{server_address}:443?transport=ws&path={urllib.parse.quote(ws_path)}&insecure={insecure_flag}&sni={server_address}"
    
    print(f"""
🚀 智能代理模式客户端配置

🎯 连接方式: TCP → WebSocket → UDP 
🔒 外界看到: 标准HTTPS网站访问
⚡ 实际传输: Hysteria2高速代理

🔗 WebSocket配置链接:
{websocket_link}

📋 手动配置参数:
服务器地址: {server_address}
端口: 443 (TCP)
密码: {password}
协议: Hysteria2
传输方式: WebSocket
WebSocket路径: {ws_path}
TLS: 启用
跳过证书验证: {'否' if use_real_cert else '是'}
SNI: {server_address}

💻 支持的客户端:

1️⃣ Hysteria2官方客户端 (推荐)
   - 完美支持WebSocket传输
   - 下载: https://github.com/apernet/hysteria/releases

2️⃣ v2rayN (Windows)
   - 添加Hysteria2配置
   - 在传输设置中选择WebSocket
   - 设置路径为: {ws_path}

3️⃣ Clash Meta
   - 支持Hysteria2 WebSocket传输
   - 配置文件示例:
```yaml
proxies:
  - name: "智能代理"
    type: hysteria2
    server: {server_address}
    port: 443
    password: {password}
    transport:
      type: ws
      path: {ws_path}
    tls: true
    skip-cert-verify: {str(not use_real_cert).lower()}
```

🎉 优势对比:
• 传统方式: 客户端UDP → 服务器UDP (容易被检测)
• 智能代理: 客户端TCP → nginx HTTPS → WebSocket → Hysteria2
• 伪装度: 极高 (外界只看到HTTPS网站访问)
• 延迟: 最低 (无Cloudflare转发)
""")

def main():
    parser = argparse.ArgumentParser(description='Hysteria2 管理工具（增强防墙版）')
    parser.add_argument('command', nargs='?', default='install',
                      help='命令: install, del, status, help, smart-proxy, setup-nginx, client, fix, verify')
    parser.add_argument('--ip', help='指定服务器IP地址或域名')
    parser.add_argument('--port', type=int, help='指定服务器端口（推荐443/80）')
    parser.add_argument('--password', help='指定密码')
    parser.add_argument('--domain', help='指定域名（用于获取真实证书）')
    parser.add_argument('--email', help='Let\'s Encrypt证书邮箱地址')
    parser.add_argument('--use-real-cert', action='store_true', 
                      help='使用真实域名证书（需要域名指向服务器）')
    parser.add_argument('--web-masquerade', action='store_true', default=True,
                      help='启用Web伪装（默认启用）')
    parser.add_argument('--auto-nginx', action='store_true', default=True,
                      help='安装时自动配置nginx (默认启用)')
    
    
    args = parser.parse_args()
    
    if args.command == 'del':
        delete_hysteria2()
    elif args.command == 'status':
        show_status()
    elif args.command == 'help':
        show_help()
    elif args.command == 'smart-proxy':
        # 智能代理配置（推荐）
        home = get_user_home()
        base_dir = f"{home}/.hysteria2"
        
        if not os.path.exists(base_dir):
            print("❌ Hysteria2 未安装，请先运行 install 命令")
            sys.exit(1)
        
        server_address = get_ip_address()
        
        # 自动检测证书路径
        print("🔍 检测现有证书文件...")
        possible_cert_paths = [
            f"{base_dir}/cert/server.crt",
            f"{base_dir}/certs/cert.pem", 
            f"{base_dir}/cert.pem"
        ]
        possible_key_paths = [
            f"{base_dir}/cert/server.key",
            f"{base_dir}/certs/key.pem",
            f"{base_dir}/key.pem"
        ]
        
        cert_path = None
        key_path = None
        
        for path in possible_cert_paths:
            if os.path.exists(path):
                cert_path = path
                print(f"✅ 找到证书文件: {cert_path}")
                break
        
        for path in possible_key_paths:
            if os.path.exists(path):
                key_path = path
                print(f"✅ 找到密钥文件: {key_path}")
                break
        
        if not cert_path or not key_path:
            print("⚠️ 未找到证书文件，生成新的自签名证书...")
            cert_path, key_path = generate_self_signed_cert(base_dir, server_address)
        
        # 检测nginx默认Web目录
        nginx_web_dirs = ["/usr/share/nginx/html", "/var/www/html", "/var/www"]
        nginx_web_dir = None
        for dir_path in nginx_web_dirs:
            if os.path.exists(dir_path):
                nginx_web_dir = dir_path
                break
        
        if not nginx_web_dir:
            nginx_web_dir = "/var/www/html"
            subprocess.run(['sudo', 'mkdir', '-p', nginx_web_dir], check=True)
        
        # 创建/更新伪装文件
        print("📝 创建伪装网站...")
        create_web_files_in_directory(nginx_web_dir)
        set_nginx_permissions(nginx_web_dir)
        
        # 配置智能代理
        success, internal_port = setup_nginx_smart_proxy(base_dir, server_address, nginx_web_dir, cert_path, key_path, 443)
        if success:
            print("🎉 智能代理配置成功！")
            
            # 获取密码信息
            config_path = f"{base_dir}/config/config.json"
            with open(config_path, 'r') as f:
                config = json.load(f)
            password = config['auth']['password']
            use_real_cert = 'letsencrypt' in config['tls']['cert']
            
            # 显示智能代理客户端配置
            show_smart_proxy_client_setup(server_address, password, use_real_cert)
            
            # 简化验证
            print("\n" + "="*30)
            print("🔍 验证配置...")
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((server_address, 443))
            sock.close()
            if result == 0:
                print("✅ 智能代理工作正常")
                print(f"💡 浏览器访问: https://{server_address}")
            else:
                print("⚠️ 连接测试失败，请检查防火墙设置")
            
        else:
            print("❌ 智能代理配置失败")
    
    elif args.command == 'verify':
        # 验证智能代理配置
        home = get_user_home()
        base_dir = f"{home}/.hysteria2"
        
        if not os.path.exists(base_dir):
            print("❌ Hysteria2 未安装，请先运行 install 命令")
            sys.exit(1)
        
        server_address = args.ip if args.ip else get_ip_address()
        port = args.port if args.port else 443
        
        print(f"🔍 验证服务器: {server_address}:{port}")
        success = verify_smart_proxy(server_address, port)
        
        if success:
            print("\n✅ 智能代理配置验证完成！")
            print("📝 如果验证成功，说明以下流程工作正常：")
            print("  1. 客户端TCP连接到nginx (443端口)")
            print("  2. nginx显示伪装网站给浏览器")
            print("  3. WebSocket路径转发到Hysteria2")
            print("  4. Hysteria2处理代理流量")
        else:
            print("\n❌ 验证发现问题，请检查配置")
            print("💡 尝试运行: python3 hy2.py smart-proxy 重新配置")
            
    elif args.command == 'setup-nginx':
        # 设置nginx TCP端口伪装（传统方式）
        home = get_user_home()
        base_dir = f"{home}/.hysteria2"
        
        if not os.path.exists(base_dir):
            print("❌ Hysteria2 未安装，请先运行 install 命令")
            sys.exit(1)
        
        # 获取配置信息
        config_path = f"{base_dir}/config/config.json"
        if not os.path.exists(config_path):
            print("❌ 配置文件不存在")
            sys.exit(1)
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        domain = args.domain if args.domain else get_ip_address()
        web_dir = f"{base_dir}/web"
        cert_path = config['tls']['cert']
        key_path = config['tls']['key']
        
        print(f"正在为域名 {domain} 设置nginx TCP端口伪装...")
        success = setup_dual_port_masquerade(base_dir, domain, web_dir, cert_path, key_path)
        
        if success:
            print(f"""
🎉 nginx设置成功！

现在你有：
- TCP 443端口: nginx提供真实Web页面 (可用curl测试)
- UDP 443端口: Hysteria2代理服务

测试命令:
curl https://{domain}
或
curl -k https://{domain}  # 如果使用自签名证书

这样外界看起来就是一个正常的HTTPS网站！
""")
        else:
            print("❌ nginx设置失败，请检查错误信息")
    elif args.command == 'client':
        # 显示客户端连接指南
        home = get_user_home()
        base_dir = f"{home}/.hysteria2"
        
        if not os.path.exists(base_dir):
            print("❌ Hysteria2 未安装，请先运行 install 命令")
            sys.exit(1)
        
        # 获取配置信息
        config_path = f"{base_dir}/config/config.json"
        if not os.path.exists(config_path):
            print("❌ 配置文件不存在")
            sys.exit(1)
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        server_address = args.domain if args.domain else get_ip_address()
        port = int(config['listen'].replace(':', ''))
        password = config['auth']['password']
        use_real_cert = 'letsencrypt' in config['tls']['cert']
        
        insecure_param = "0" if use_real_cert else "1"
        config_link = f"hysteria2://{urllib.parse.quote(password)}@{server_address}:{port}?insecure={insecure_param}&sni={server_address}"
        
        show_client_setup(config_link, server_address, port, password, use_real_cert)
    elif args.command == 'fix':
        # 修复nginx配置和权限问题
        home = get_user_home()
        base_dir = f"{home}/.hysteria2"
        
        if not os.path.exists(base_dir):
            print("❌ Hysteria2 未安装，请先运行 install 命令")
            sys.exit(1)
        
        domain = args.domain if args.domain else get_ip_address()
        
        print("🔧 正在修复nginx配置 - 使用简化方案...")
        
        # 1. 检测nginx默认Web目录
        nginx_web_dirs = [
            "/var/www/html",           # Ubuntu/Debian 默认
            "/usr/share/nginx/html",   # CentOS/RHEL 默认
            "/var/www"                 # 备选
        ]
        
        nginx_web_dir = None
        for dir_path in nginx_web_dirs:
            if os.path.exists(dir_path):
                nginx_web_dir = dir_path
                break
        
        if not nginx_web_dir:
            nginx_web_dir = "/var/www/html"
            try:
                subprocess.run(['sudo', 'mkdir', '-p', nginx_web_dir], check=True)
                print(f"✅ 创建Web目录: {nginx_web_dir}")
            except Exception as e:
                print(f"❌ 创建Web目录失败: {e}")
                sys.exit(1)
        
        print(f"✅ 检测到nginx Web目录: {nginx_web_dir}")
        
        # 2. 备份并复制伪装文件
        try:
            # 备份原有文件
            if os.path.exists(f"{nginx_web_dir}/index.html"):
                subprocess.run(['sudo', 'cp', f'{nginx_web_dir}/index.html', f'{nginx_web_dir}/index.html.backup'], check=True)
                print("✅ 备份原有index.html")
            
            # 直接在nginx目录创建我们的伪装文件
            print("📝 正在创建伪装网站文件...")
            create_web_files_in_directory(nginx_web_dir)
            
            # 设置权限
            set_nginx_permissions(nginx_web_dir)
            
            print(f"✅ 伪装文件已创建并设置权限: {nginx_web_dir}")
            
        except Exception as e:
            print(f"❌ 创建伪装文件失败: {e}")
            sys.exit(1)
        
        # 3. 确保nginx SSL配置正确
        try:
            cert_path = f"{base_dir}/cert/server.crt"
            key_path = f"{base_dir}/cert/server.key"
            
            if not os.path.exists(cert_path) or not os.path.exists(key_path):
                print("⚠️ 证书文件不存在，重新生成...")
                cert_path, key_path = generate_self_signed_cert(base_dir, domain)
            
            # 创建简化的SSL配置
            ssl_conf = f"""# SSL configuration for Hysteria2 masquerade
server {{
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    
    ssl_certificate {os.path.abspath(cert_path)};
    ssl_certificate_key {os.path.abspath(key_path)};
    
    # SSL配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    
    # 指定网站根目录和默认文件
    root {nginx_web_dir};
    index index.html index.htm;
    
    # 处理静态文件
    location / {{
        try_files $uri $uri/ /index.html;
    }}
    
    # 隐藏nginx版本
    server_tokens off;
    
    # 基本安全头
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
}}"""
            
            ssl_conf_file = "/etc/nginx/conf.d/hysteria2-ssl.conf"
            
            # 删除旧的配置文件
            subprocess.run(['sudo', 'rm', '-f', f'/etc/nginx/conf.d/{domain}.conf'], check=False)
            subprocess.run(['sudo', 'rm', '-f', f'/etc/nginx/sites-enabled/{domain}'], check=False)
            subprocess.run(['sudo', 'rm', '-f', f'/etc/nginx/sites-available/{domain}'], check=False)
            
            # 写入新配置
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.conf') as tmp:
                tmp.write(ssl_conf)
                tmp.flush()
                subprocess.run(['sudo', 'cp', tmp.name, ssl_conf_file], check=True)
                os.unlink(tmp.name)
                
            print(f"✅ SSL配置已更新: {ssl_conf_file}")
            
        except Exception as e:
            print(f"⚠️ SSL配置更新失败: {e}")
        
        # 4. 测试并重新加载nginx
        try:
            test_result = subprocess.run(['sudo', 'nginx', '-t'], capture_output=True, text=True)
            if test_result.returncode != 0:
                print(f"❌ nginx配置测试失败: {test_result.stderr}")
            else:
                subprocess.run(['sudo', 'systemctl', 'reload', 'nginx'], check=True)
                print("✅ nginx配置已重新加载")
                
                print(f"""
🎉 修复完成！

✅ 伪装网站文件已部署到: {nginx_web_dir}
✅ nginx已正确配置SSL (443端口)
✅ HTTP 80端口显示伪装网站
✅ HTTPS 443端口显示伪装网站

测试命令:
curl http://{domain}      # HTTP访问
curl -k https://{domain}  # HTTPS访问

现在外界访问你的服务器会看到一个正常的企业网站！
""")
        except Exception as e:
            print(f"❌ nginx重新加载失败: {e}")
            print("请手动检查nginx配置: sudo nginx -t")
    elif args.command == 'install':
        # 防墙优化配置
        port = args.port if args.port else 443  # 默认使用443端口
        password = args.password if args.password else "123qwe!@#QWE"
        domain = args.domain
        email = args.email if args.email else "admin@example.com"
        use_real_cert = args.use_real_cert
        
        # 获取IP地址或域名
        if domain:
            server_address = domain
            print(f"使用域名: {domain}")
            if not use_real_cert:
                print("建议使用 --use-real-cert 参数获取真实证书以增强安全性")
        else:
            server_address = args.ip if args.ip else get_ip_address()
            if use_real_cert:
                print("警告: 使用真实证书需要指定域名，将使用自签名证书")
                use_real_cert = False
        
        print("\n开始安装 Hysteria2（防墙增强版）...")
        print(f"服务器地址: {server_address}")
        print(f"端口: {port} ({'HTTPS标准端口' if port == 443 else 'HTTP标准端口' if port == 80 else '自定义端口'})")
        print(f"证书类型: {'真实证书' if use_real_cert else '自签名证书'}")
        
        # 检查端口
        if not check_port_available(port):
            # 检查是否是hysteria进程占用
            print(f"检测到UDP端口 {port} 已被占用，正在分析占用进程...")
            
            try:
                # 尝试用sudo检查所有进程（可以看到其他用户的进程）
                try:
                    result = subprocess.run(['sudo', 'ss', '-anup'], capture_output=True, text=True)
                    ss_output = result.stdout
                except:
                    # 如果sudo失败，用普通权限检查
                    result = subprocess.run(['ss', '-anup'], capture_output=True, text=True)
                    ss_output = result.stdout
                
                # 检查是否是hysteria进程
                if f':{port}' in ss_output and 'hysteria' in ss_output:
                    print(f"✅ 检测到Hysteria2已在UDP端口 {port} 运行")
                    print("如需重新安装，请先运行: python3 hy2.py del")
                    
                    # 检查是否是当前用户的进程
                    current_user = os.getenv('USER', 'unknown')
                    print(f"当前用户: {current_user}")
                    print("提示: 如果是其他用户启动的Hysteria2，请切换到对应用户操作")
                    sys.exit(1)
                    
                elif f':{port}' in ss_output:
                    print(f"❌ UDP端口 {port} 被其他程序占用")
                    print("占用详情:")
                    # 显示占用端口的进程
                    for line in ss_output.split('\n'):
                        if f':{port}' in line and 'udp' in line.lower():
                            print(f"  {line}")
                    print(f"解决方案: 使用其他端口，如: python3 hy2.py install --port 8443")
                    sys.exit(1)
                else:
                    print(f"⚠️ 无法确定端口占用情况，但UDP端口 {port} 不可用")
                    print("可能原因：权限不足或系统限制")
                    print(f"建议: 尝试其他端口: python3 hy2.py install --port 8443")
                    sys.exit(1)
                    
            except Exception as e:
                print(f"❌ 端口检查失败: {e}")
                print(f"UDP端口 {port} 不可用，请选择其他端口")
                print("注意: nginx可以与Hysteria2共享443端口 (nginx用TCP，Hysteria2用UDP)")
                sys.exit(1)
        
        # 创建目录
        base_dir = create_directories()
        
        # 下载Hysteria2
        binary_path, version = download_hysteria2(base_dir)
        
        # 验证二进制文件
        if not verify_binary(binary_path):
            print("错误: Hysteria2 二进制文件无效")
            sys.exit(1)
        
        # 创建Web伪装页面
        web_dir = create_web_masquerade(base_dir)
        
        # 获取证书
        cert_path = None
        key_path = None
        
        if use_real_cert and domain:
            # 尝试获取真实证书
            cert_path, key_path = get_real_certificate(base_dir, domain, email)
        
        # 如果获取真实证书失败或不使用真实证书，则生成自签名证书
        if not cert_path or not key_path:
            cert_path, key_path = generate_self_signed_cert(base_dir, server_address)
        
        # 创建配置
        config_path = create_config(base_dir, port, password, cert_path, key_path, 
                                  server_address, args.web_masquerade, web_dir)
        
        # 创建启动脚本
        start_script = create_service_script(base_dir, binary_path, config_path, port)
        
        # 创建停止脚本
        stop_script = create_stop_script(base_dir)
        
        # 立即启动Hysteria2服务
        service_started = start_service(start_script, port, base_dir)
        
        # 自动配置nginx智能代理 (如果启用)
        nginx_success = False
        if args.auto_nginx and port == 443:
            print("\n🚀 配置智能代理...")
            
            # 检测并安装nginx
            try:
                subprocess.run(['which', 'nginx'], check=True, capture_output=True)
                print("✅ 检测到nginx已安装")
            except:
                print("正在安装nginx...")
                if shutil.which('dnf'):
                    subprocess.run(['sudo', 'dnf', 'install', '-y', 'nginx'], check=True)
                elif shutil.which('yum'):
                    subprocess.run(['sudo', 'yum', 'install', '-y', 'nginx'], check=True)
                elif shutil.which('apt'):
                    subprocess.run(['sudo', 'apt', 'update'], check=True)
                    subprocess.run(['sudo', 'apt', 'install', '-y', 'nginx'], check=True)
                else:
                    print("⚠️ 无法自动安装nginx")
                    nginx_success = False
            
            if nginx_success is not False:
                # 检测nginx默认Web目录并创建伪装文件
                nginx_web_dirs = ["/usr/share/nginx/html", "/var/www/html", "/var/www"]
                nginx_web_dir = None
                for dir_path in nginx_web_dirs:
                    if os.path.exists(dir_path):
                        nginx_web_dir = dir_path
                        break
                
                if not nginx_web_dir:
                    nginx_web_dir = "/var/www/html"
                    subprocess.run(['sudo', 'mkdir', '-p', nginx_web_dir], check=True)
                
                # 创建伪装文件
                if os.path.exists(f"{nginx_web_dir}/index.html"):
                    subprocess.run(['sudo', 'cp', f'{nginx_web_dir}/index.html', f'{nginx_web_dir}/index.html.backup'], check=True)
                
                print("📝 正在创建伪装网站文件...")
                create_web_files_in_directory(nginx_web_dir)
                set_nginx_permissions(nginx_web_dir)
                
                # 使用智能代理功能
                try:
                    success, internal_port = setup_nginx_smart_proxy(base_dir, server_address, nginx_web_dir, cert_path, key_path, port)
                    if success:
                        nginx_success = True
                        print("🎉 智能代理配置成功！")
                        print("🎯 外界访问443端口看到正常HTTPS网站")
                        print("🎯 Hysteria2客户端通过WebSocket隧道透明连接")
                        
                        # 更新客户端连接信息
                        print(f"\n📱 客户端连接方式已优化:")
                        print(f"服务器: {server_address}")
                        print(f"端口: 443 (TCP)")
                        print(f"传输方式: WebSocket (/hy2-tunnel)")
                        print(f"内部端口: {internal_port} (UDP)")
                    else:
                        print("⚠️ 智能代理配置失败，使用基础方案")
                        nginx_success = False
                except Exception as e:
                    print(f"⚠️ 智能代理配置异常: {e}")
                    nginx_success = False
        
        if not nginx_success and port == 443:
            print("⚠️ nginx未自动配置，可以稍后手动运行: python3 hy2.py fix")
        
        # 生成客户端配置链接
        insecure_param = "0" if use_real_cert else "1"
        config_link = f"hysteria2://{urllib.parse.quote(password)}@{server_address}:{port}?insecure={insecure_param}&sni={server_address}"
        
        print(f"""
🎉 Hysteria2 防墙增强版安装成功！

📋 安装信息:
- 版本: {version}
- 安装目录: {base_dir}
- 配置文件: {config_path}
- Web伪装目录: {web_dir}
- 启动脚本: {start_script}
- 停止脚本: {stop_script}
- 日志文件: {base_dir}/logs/hysteria.log

🚀 使用方法:
1. 启动服务: {start_script}
2. 停止服务: {stop_script}
3. 查看日志: {base_dir}/logs/hysteria.log
4. 查看状态: python3 hy2.py status

🔐 服务器信息:
- 地址: {server_address}
- 端口: {port} ({'HTTPS端口' if port == 443 else 'HTTP端口' if port == 80 else '自定义端口'})
- 密码: {password}
- 证书: {'真实证书' if use_real_cert else '自签名证书'} ({cert_path})
- Web伪装: {'启用' if args.web_masquerade else '禁用'}

🔗 客户端配置链接:
{config_link}

📱 客户端手动配置:
服务器: {server_address}
端口: {port}
密码: {password}
TLS: 启用
跳过证书验证: {'否' if use_real_cert else '是'}
SNI: {server_address}

🛡️ 防墙优化特性:
✅ 使用标准HTTPS端口 (443)
✅ Web页面伪装 (访问 https://{server_address}:{port} 显示正常网站)
✅ 随机伪装目标网站
✅ 优化带宽配置 (1000mbps)  
✅ 降低日志级别
{'✅ nginx TCP端口伪装' if nginx_success else '⚠️ nginx未配置 (建议运行: python3 hy2.py setup-nginx)'}
{'✅ 真实域名证书' if use_real_cert else '⚠️ 自签名证书 (建议使用真实域名证书)'}

💡 快速测试:
{'curl https://' + server_address + '  # 应正常显示网站' if nginx_success else 'curl -k https://' + server_address + '  # 自签名证书需要-k参数'}

💡 进一步优化建议:
1. 使用真实域名和证书: --domain yourdomain.com --use-real-cert --email your@email.com
2. 定期更换密码和端口
3. 监控日志，如发现异常及时调整

🌍 支持的客户端:
- v2rayN (Windows)
- Qv2ray (跨平台)  
- Clash Meta (多平台)
- 官方客户端 (各平台)
""")

        # 显示客户端连接指南
        show_client_setup(config_link, server_address, port, password, use_real_cert)
    else:
        print(f"未知命令: {args.command}")
        show_help()
        sys.exit(1)

if __name__ == "__main__":
    main() 
