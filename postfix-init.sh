#!/bin/bash
# Configure Postfix with SendGrid credentials

# Set relay host and credentials
postconf -e "relayhost = [smtp.sendgrid.net]:587"
postconf -e "smtp_sasl_auth_enable = yes"
postconf -e "smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd"
postconf -e "smtp_sasl_security_options = noanonymous"
postconf -e "smtp_use_tls = yes"

# Create SASL password map file
cat > /etc/postfix/sasl_passwd << EOF
[smtp.sendgrid.net]:587 ${SMTP_USERNAME}:${SMTP_PASSWORD}
EOF

# Set permissions
chmod 600 /etc/postfix/sasl_passwd
postmap /etc/postfix/sasl_passwd

# Start Postfix
/etc/init.d/postfix start
tail -f /var/log/mail.log
