# Reproduce

## Before fix
- GET http://100.76.217.35:8001/cloud/login -> 200, form action /cloud/login, field name email type email, csrf_token hidden
- POST email=user1 password=123 -> 400 Bad Request, body contains "Email or password is incorrect." (form-error-summary), no session cookie, no redirect
- POST email=user1@demo.local password=123 -> 302 Found location /cloud/instances, set-cookie mosh_session (redacted)

## After fix
- GET http://100.76.217.35:8001/cloud/login -> 200, form action /cloud/login, field name email type text (now accepts username), csrf_token hidden, inputmode email
- POST email=user1 password=123 -> 302 Found location /cloud/instances, set-cookie mosh_session (redacted)
- POST email=user1@demo.local password=123 -> 302 Found location /cloud/instances, set-cookie mosh_session (redacted)
- POST email=USER1 (case) password=123 -> 302
- POST email="  user1  " (whitespace) password=123 -> 302
- POST email=user1 password=wrong -> 400 "Email or password is incorrect."
- POST email=unknown_user_xyz password=123 -> 400 "Email or password is incorrect." (same message, no enumeration)
