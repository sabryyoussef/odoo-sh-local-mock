# Helpers ERP Public Tenant Routing — Deployment

## Current Status

Tenant 32 (hms_28_74d22b) has been configured for public routing:
- Real domain: `sabry.serveirc.com`
- Real public URL: `https://hms-28-74d22b.sabry.serveirc.com`
- Real internal Odoo: `http://127.0.0.1:8217`

## Configuration Files

Generated Nginx configuration:
- Location: `/tmp/helpers-erp-tenants-dynamic.conf`
- Contains: 17 active tenants, upstream definitions, routing rules
- Format: Nginx conf.d style (include in http{} block)

## Deployment Steps

### Step 1: Deploy Nginx Configuration

```bash
sudo cp /tmp/helpers-erp-tenants-dynamic.conf /etc/nginx/conf.d/helpers-erp-tenants.conf
```

### Step 2: Verify Configuration

```bash
sudo nginx -t
```

Expected output:
```
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

### Step 3: Reload Nginx

```bash
sudo systemctl reload nginx
```

### Step 4: Verify Nginx is Running

```bash
sudo systemctl status nginx
curl -I https://hms-28-74d22b.sabry.serveirc.com/web/login \
  -k --resolve hms-28-74d22b.sabry.serveirc.com:443:192.168.1.5
```

Expected: HTTP 200 (may be 302 redirect depending on Odoo config)

## Verification

### DNS (if available externally)

```bash
nslookup hms-28-74d22b.sabry.serveirc.com
```

Should resolve to public IP of sabry.serveirc.com

### HTTPS Certificate

```bash
openssl s_client -connect hms-28-74d22b.sabry.serveirc.com:443 -servername hms-28-74d22b.sabry.serveirc.com
```

Should show valid certificate for `sabry.serveirc.com`

### Odoo Access

From external client (if DNS works):
```
https://hms-28-74d22b.sabry.serveirc.com/web/login
```

Should load Helpers HMS login page

## Database Update

Tenant 32 has been updated in SQLite:
- `public_url` changed from placeholder to real URL
- `internal_url` remains `http://192.168.1.7:8069/`

## Environment Variables

Updated .env:
- `TENANT_PUBLIC_BASE_URL=https://sabry.serveirc.com`
- New tenants will automatically get real public URLs

## Maintenance

To update the Nginx config when new tenants are added:

```bash
cd /opt/projects/active/odoo-sh-local-mock
python3 generate-tenant-nginx-config.py
sudo cp /tmp/helpers-erp-tenants-dynamic.conf /etc/nginx/conf.d/helpers-erp-tenants.conf
sudo systemctl reload nginx
```

## Rollback

To rollback if needed:

```bash
sudo rm /etc/nginx/conf.d/helpers-erp-tenants.conf
sudo systemctl reload nginx
```

Original placeholder config in database:
- Tenant 32 `public_url`: `https://hms-28-74d22b.apps.example.com` (backed up in notes)
