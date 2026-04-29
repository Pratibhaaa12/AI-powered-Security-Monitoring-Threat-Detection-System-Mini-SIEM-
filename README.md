# AI-powered-Security-Monitoring-Threat-Detection-System-Mini-SIEM-

### Security Features
- Real-time security event logging (login, logout, failed login, API access)
- Detection of unauthorized access attempts (401/403 monitoring)
- Rate-limiting abuse detection (429 monitoring)
- Suspicious IP and anomaly detection
- Brute-force attack detection simulation
- Commit activity spike detection

### Monitoring & Alerting
- Security dashboard for visualizing threats
- Rule-based alert engine (brute force, anomaly, API abuse)
- Slack integration for real-time security alerts
- Audit trail for admin and system actions

### System Features
- JWT authentication and RBAC-based access control
- Middleware-based security monitoring
- OAuth integration (GitHub, Google)
- AI-based commit and activity analysis
 
 ### Architecture Flow

User/Login/API Request  
→ Middleware (Security Monitoring Layer)  
→ SecurityEventLog Database  
→ Rule Engine (Threat Detection)  
→ Alert System (Slack Notifications)  
→ Security Dashboard Visualization



- Backend: Django, Django REST Framework  
- Database: PostgreSQL  
- Caching/Queue: Redis  
- Security: JWT, RBAC, OAuth  
- Integrations: GitHub API, Slack API  
- Others: Python, Docker



- OWASP Top 10 awareness  
- Authentication & Authorization security  
- API security monitoring  
- Log analysis and audit tracking  
- Anomaly detection system  
- Threat detection rules engine
-
-
  ### Detected Threats
- Brute force login attempts
- Suspicious IP changes
- Unauthorized API access attempts
- Abnormal API usage patterns
- Commit activity spikes (possible insider anomaly)
- Security event lifecycle tracking



### Security Dashboard

The system provides a centralized dashboard to visualize:
- Failed login attempts
- Unauthorized access logs
- API abuse patterns
- Security alerts timeline
- Anomaly detection reports

### Testing & Simulation

The system includes simulated attack scenarios:
- Login brute force simulation
- API flooding simulation
- Unauthorized access testing
- Rate-limit abuse testing

These simulations validate the effectiveness of detection and alert mechanisms.



### Setup Instruction

- pip install -r requirements.txt
- python manage.py migrate
- python manage.py runserver
