#!/usr/bin/env python3
"""
Generate Nginx configuration for Helpers ERP tenant routing.
Queries the database for active tenants and generates dynamic reverse proxy config.
Generates a snippet intended for inclusion in nginx.conf http{} block.
"""

import sqlite3
import sys

DATABASE = "data/control.db"
DOMAIN = "sabry.serveirc.com"
OUTPUT_FILE = "/tmp/helpers-erp-tenants-dynamic.conf"

def get_active_tenants():
    """Fetch active tenants with their ports from database."""
    try:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Query active tenants with their HTTP ports
        cursor.execute("""
            SELECT 
                tenant_code,
                http_port,
                status
            FROM tenants
            WHERE status = 'active' AND http_port IS NOT NULL
            ORDER BY id
        """)
        
        tenants = cursor.fetchall()
        conn.close()
        return tenants
    except Exception as e:
        print(f"Error querying database: {e}", file=sys.stderr)
        return []

def generate_config(tenants):
    """Generate Nginx configuration snippet (http{} block compatible)."""
    
    # Header - this should be included in http{} block
    config = f"""# Helpers ERP Multi-Tenant Routing Configuration
# Place this in /etc/nginx/conf.d/helpers-erp-tenants.conf
# or include it in nginx.conf http{{}} block
# Auto-generated configuration for tenant reverse proxy
# Domain: {DOMAIN}

map $http_upgrade $connection_upgrade {{
    default upgrade;
    ''      close;
}}

"""
    
    # Generate upstream servers
    config += "# Upstream Odoo tenant instances\n"
    for tenant in tenants:
        tenant_code = tenant['tenant_code'].replace('_', '-')
        port = tenant['http_port']
        config += f"upstream tenant_{tenant_code} {{\n"
        config += f"    server 127.0.0.1:{port};\n"
        config += "}}\n\n"
    
    # Generate host-to-upstream mapping
    config += "# Map hostname to upstream server\n"
    config += f"map $http_host $tenant_backend {{\n"
    for tenant in tenants:
        tenant_code = tenant['tenant_code'].replace('_', '-')
        config += f'    "{tenant_code}.{DOMAIN}" "tenant_{tenant_code}";\n'
    config += '    default "";\n'
    config += "}\n\n"
    
    # HTTP redirect server block
    config += f"""# HTTP to HTTPS redirect for tenant subdomains
server {{
    listen 192.168.1.5:80;
    server_name *.{DOMAIN};

    location /.well-known/acme-challenge/ {{
        root /var/www/certbot;
        allow all;
    }}

    location / {{
        return 301 https://$host$request_uri;
    }}
}}

"""
    
    # HTTPS routing server block
    config += f"""# HTTPS tenant routing - routes tenant subdomains to their Odoo instances
server {{
    listen 192.168.1.5:443 ssl;
    server_name ~^(?<subdomain>[a-z0-9-]+)\\.{DOMAIN.replace(".", "\\.")}$;

    ssl_certificate     /etc/ssl/certs/{DOMAIN}.crt;
    ssl_certificate_key /etc/ssl/private/{DOMAIN}.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    location / {{
        set $backend $tenant_backend;
        
        if ($backend = "") {{
            return 404 "Tenant not found or not provisioned";
        }}

        proxy_pass http://$backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        
        proxy_read_timeout 720s;
        proxy_send_timeout 720s;
        proxy_connect_timeout 30s;
        
        client_max_body_size 200M;
        proxy_buffering on;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
    }}
}}
"""
    
    return config

def main():
    tenants = get_active_tenants()
    
    if not tenants:
        print("No active tenants found", file=sys.stderr)
        return 1
    
    print(f"Found {len(tenants)} active tenants:")
    for tenant in tenants:
        print(f"  - {tenant['tenant_code']} on port {tenant['http_port']}")
    
    config = generate_config(tenants)
    
    with open(OUTPUT_FILE, 'w') as f:
        f.write(config)
    
    print(f"\nConfiguration written to {OUTPUT_FILE}")
    print(f"To apply:")
    print(f"  1. sudo cp {OUTPUT_FILE} /etc/nginx/conf.d/helpers-erp-tenants.conf")
    print(f"  2. sudo systemctl reload nginx")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
