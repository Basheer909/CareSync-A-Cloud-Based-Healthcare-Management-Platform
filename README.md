# CareSync — Patient-Doctor Appointment & Medication Management System

CareSync is a cloud-enabled healthcare management app built with Flask, AWS DynamoDB, and AWS SNS.
Patients can book real doctor availability slots, track medications with reminders, and log vitals.
Doctors manage their availability and issue prescriptions.

##  Project Structure
```
caresync/
├── app.py
├── requirements.txt
├── .env.example
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── login.html
│   ├── signup.html
│   ├── patientdashboard.html
│   ├── doctordashboard.html
│   ├── appointment.html
│   ├── addmedication.html
│   ├── prescribe.html
│   ├── aboutus.html
│   └── contactus.html
└── static/
    └── styles.css
```

##  Local Setup

```bash
git clone <your-repo-url>
cd caresync

python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env          # fill in your AWS credentials
python app.py
```

Runs at `http://127.0.0.1:5000`.

##  AWS Setup

### 1. IAM
Create an IAM user (or role) with programmatic access and a policy allowing:
- `dynamodb:PutItem`, `GetItem`, `UpdateItem`, `Query`, `Scan` on the tables below
- `sns:Publish` on your topic

### 2. DynamoDB Tables

| Table | Partition Key | Notes |
|---|---|---|
| `CareSync_Users` | `email` (String) | role = patient / doctor |
| `CareSync_DoctorSlots` | `id` (String) | GSI: `doctor_email-index` (partition key `doctor_email`) |
| `CareSync_Appointments` | `id` (String) | GSI: `patient_email-index`, GSI: `doctor_email-index` |
| `CareSync_Medications` | `id` (String) | GSI: `patient_email-index` |
| `CareSync_Vitals` | `id` (String) | GSI: `patient_email-index` |

To add a GSI in the console: open the table → **Indexes** tab → **Create index** →
set the partition key (e.g. `patient_email`, type String) → keep default projection (All).

### 3. SNS Topic
- Create a **Standard** topic named `CareSyncAlerts`
- Subscribe your email (protocol: Email) and confirm the subscription
- Copy the Topic ARN into `.env` as `SNS_TOPIC_ARN`

##  Deployment (optional — EC2)
```bash
pip install gunicorn
gunicorn --bind 0.0.0.0:8000 app:app
```
Attach the IAM role with DynamoDB/SNS permissions directly to the EC2 instance
instead of using access keys in production.

##  Technologies Used
- **Backend:** Python, Flask
- **Cloud:** AWS DynamoDB, AWS SNS, IAM
- **Frontend:** HTML, CSS, Jinja2

##  Notification Flow
1. Patient signs up / books an appointment / sets a medication reminder / doctor adds a prescription
2. Event is written to DynamoDB
3. SNS publishes a notification to the topic
4. Subscribed email receives the alert

##  My Contributions
- Designed the DynamoDB schema (5 tables, 3 GSIs) and IAM permissions
- Built the full Flask app: auth, role-based dashboards, slot-based booking, medication reminders, vitals tracking
- Integrated AWS SNS for event-driven notifications
- Styled the frontend with a custom CSS design system
- Wrote setup documentation for local + AWS deployment

