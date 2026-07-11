"""
CareSync - Patient-Doctor Appointment & Medication Management System
Flask + AWS DynamoDB + AWS SNS

Author: Md Basheer Khan
"""

import os
import uuid
from datetime import datetime

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN")

# ---------------------------------------------------------------------------
# AWS clients / DynamoDB tables
# ---------------------------------------------------------------------------
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
sns = boto3.client("sns", region_name=AWS_REGION)

users_table = dynamodb.Table("CareSync_Users")           # pk: email
slots_table = dynamodb.Table("CareSync_DoctorSlots")     # pk: id   (GSI: doctor_email)
appointments_table = dynamodb.Table("CareSync_Appointments")  # pk: id (GSI: patient_email, doctor_email)
medications_table = dynamodb.Table("CareSync_Medications")    # pk: id (GSI: patient_email)
vitals_table = dynamodb.Table("CareSync_Vitals")          # pk: id  (GSI: patient_email)


def notify(subject: str, message: str):
    """Publish a notification to the SNS topic. Fails silently if SNS isn't configured."""
    if not SNS_TOPIC_ARN:
        print(f"[SNS skipped - no topic configured] {subject}: {message}")
        return
    try:
        sns.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject, Message=message)
    except ClientError as e:
        print(f"[SNS publish failed] {e}")


def wants_json():
    """True if the request came from our fetch()-based JS rather than a plain form post."""
    return request.headers.get("X-Requested-With") == "fetch" or request.is_json


def login_required(role=None):
    """Simple decorator factory to guard routes by session + optional role."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            if "email" not in session:
                flash("Please log in first.", "error")
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                flash("You don't have access to that page.", "error")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/about")
def about():
    return render_template("aboutus.html")


@app.route("/contact")
def contact():
    return render_template("contactus.html")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        name = request.form["name"].strip()
        password = request.form["password"]
        role = request.form.get("role", "patient")  # patient or doctor
        specialization = request.form.get("specialization", "")

        existing = users_table.get_item(Key={"email": email}).get("Item")
        if existing:
            flash("An account with this email already exists.", "error")
            return redirect(url_for("signup"))

        users_table.put_item(Item={
            "email": email,
            "name": name,
            "password_hash": generate_password_hash(password),
            "role": role,
            "specialization": specialization,
            "created_at": datetime.utcnow().isoformat(),
        })

        notify("New CareSync Signup", f"{name} ({email}) registered as {role}.")
        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        user = users_table.get_item(Key={"email": email}).get("Item")
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        session["email"] = user["email"]
        session["name"] = user["name"]
        session["role"] = user["role"]

        return redirect(
            url_for("doctor_dashboard") if user["role"] == "doctor" else url_for("patient_dashboard")
        )

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Patient side
# ---------------------------------------------------------------------------
def _patient_data(email):
    appts = appointments_table.query(
        IndexName="patient_email-index",
        KeyConditionExpression=Key("patient_email").eq(email),
    ).get("Items", [])
    meds = medications_table.query(
        IndexName="patient_email-index",
        KeyConditionExpression=Key("patient_email").eq(email),
    ).get("Items", [])
    vitals = sorted(
        vitals_table.query(
            IndexName="patient_email-index",
            KeyConditionExpression=Key("patient_email").eq(email),
        ).get("Items", []),
        key=lambda v: v.get("logged_at", ""),
    )
    return appts, meds, vitals


@app.route("/patient/dashboard")
@login_required(role="patient")
def patient_dashboard():
    appts, meds, vitals = _patient_data(session["email"])
    return render_template("patientdashboard.html", appointments=appts, medications=meds, vitals=vitals)


@app.route("/api/patient/data")
@login_required(role="patient")
def api_patient_data():
    appts, meds, vitals = _patient_data(session["email"])
    return jsonify(appointments=appts, medications=meds, vitals=vitals)


@app.route("/appointment/book", methods=["GET", "POST"])
@login_required(role="patient")
def book_appointment():
    if request.method == "POST":
        slot_id = request.form["slot_id"]
        slot = slots_table.get_item(Key={"id": slot_id}).get("Item")

        if not slot or slot.get("booked"):
            if wants_json():
                return jsonify(ok=False, error="That slot is no longer available."), 409
            flash("That slot is no longer available.", "error")
            return redirect(url_for("book_appointment"))

        appt_id = str(uuid.uuid4())
        appointments_table.put_item(Item={
            "id": appt_id,
            "patient_email": session["email"],
            "doctor_email": slot["doctor_email"],
            "date": slot["date"],
            "time": slot["time"],
            "status": "confirmed",
            "created_at": datetime.utcnow().isoformat(),
        })

        slots_table.update_item(
            Key={"id": slot_id},
            UpdateExpression="SET booked = :b, patient_email = :p",
            ExpressionAttributeValues={":b": True, ":p": session["email"]},
        )

        notify(
            "Appointment Confirmed",
            f"{session['name']} booked an appointment with {slot['doctor_email']} on {slot['date']} at {slot['time']}."
        )

        if wants_json():
            return jsonify(ok=True, message=f"Booked with {slot['doctor_email']} on {slot['date']} at {slot['time']}.")
        flash("Appointment booked!", "success")
        return redirect(url_for("patient_dashboard"))

    open_slots = slots_table.scan(
        FilterExpression=Attr("booked").eq(False)
    ).get("Items", [])
    return render_template("appointment.html", slots=open_slots)


@app.route("/api/slots")
@login_required(role="patient")
def api_slots():
    open_slots = slots_table.scan(
        FilterExpression=Attr("booked").eq(False)
    ).get("Items", [])
    open_slots.sort(key=lambda s: (s.get("date", ""), s.get("time", "")))
    return jsonify(slots=open_slots)


@app.route("/medication/add", methods=["GET", "POST"])
@login_required(role="patient")
def add_medication():
    if request.method == "POST":
        med_id = str(uuid.uuid4())
        medications_table.put_item(Item={
            "id": med_id,
            "patient_email": session["email"],
            "name": request.form["name"],
            "dosage": request.form["dosage"],
            "time_of_day": request.form["time_of_day"],
            "reminder": request.form.get("reminder") == "on",
            "created_at": datetime.utcnow().isoformat(),
        })

        if request.form.get("reminder") == "on":
            notify(
                "Medication Reminder Set",
                f"Reminder set for {session['name']}: {request.form['name']} "
                f"({request.form['dosage']}) at {request.form['time_of_day']}."
            )

        if wants_json():
            return jsonify(ok=True, message=f"{request.form['name']} added to your medications.")
        flash("Medication added.", "success")
        return redirect(url_for("patient_dashboard"))

    return render_template("addmedication.html")


@app.route("/vitals/log", methods=["POST"])
@login_required(role="patient")
def log_vitals():
    vitals_table.put_item(Item={
        "id": str(uuid.uuid4()),
        "patient_email": session["email"],
        "bp": request.form.get("bp", ""),
        "sugar": request.form.get("sugar", ""),
        "weight": request.form.get("weight", ""),
        "logged_at": datetime.utcnow().isoformat(),
    })
    if wants_json():
        return jsonify(ok=True, message="Vitals logged.")
    flash("Vitals logged.", "success")
    return redirect(url_for("patient_dashboard"))


# ---------------------------------------------------------------------------
# Doctor side
# ---------------------------------------------------------------------------
@app.route("/doctor/dashboard")
@login_required(role="doctor")
def doctor_dashboard():
    email = session["email"]

    appts = appointments_table.query(
        IndexName="doctor_email-index",
        KeyConditionExpression=Key("doctor_email").eq(email),
    ).get("Items", [])

    slots = slots_table.query(
        IndexName="doctor_email-index",
        KeyConditionExpression=Key("doctor_email").eq(email),
    ).get("Items", [])

    return render_template("doctordashboard.html", appointments=appts, slots=slots)


@app.route("/api/doctor/data")
@login_required(role="doctor")
def api_doctor_data():
    email = session["email"]
    appts = appointments_table.query(
        IndexName="doctor_email-index",
        KeyConditionExpression=Key("doctor_email").eq(email),
    ).get("Items", [])
    slots = slots_table.query(
        IndexName="doctor_email-index",
        KeyConditionExpression=Key("doctor_email").eq(email),
    ).get("Items", [])
    slots.sort(key=lambda s: (s.get("date", ""), s.get("time", "")))
    return jsonify(appointments=appts, slots=slots)


@app.route("/doctor/slots/add", methods=["POST"])
@login_required(role="doctor")
def add_slot():
    slots_table.put_item(Item={
        "id": str(uuid.uuid4()),
        "doctor_email": session["email"],
        "date": request.form["date"],
        "time": request.form["time"],
        "booked": False,
    })
    if wants_json():
        return jsonify(ok=True, message=f"Slot added for {request.form['date']} at {request.form['time']}.")
    flash("Availability slot added.", "success")
    return redirect(url_for("doctor_dashboard"))


@app.route("/doctor/prescribe/<appointment_id>", methods=["GET", "POST"])
@login_required(role="doctor")
def prescribe(appointment_id):
    appt = appointments_table.get_item(Key={"id": appointment_id}).get("Item")
    if not appt:
        flash("Appointment not found.", "error")
        return redirect(url_for("doctor_dashboard"))

    if request.method == "POST":
        appointments_table.update_item(
            Key={"id": appointment_id},
            UpdateExpression="SET prescription = :p, #s = :status",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":p": request.form["prescription"], ":status": "completed"},
        )
        notify(
            "Prescription Added",
            f"Dr. {session['name']} added a prescription for {appt['patient_email']}."
        )
        flash("Prescription saved.", "success")
        return redirect(url_for("doctor_dashboard"))

    return render_template("prescribe.html", appointment=appt)


if __name__ == "__main__":
    app.run(debug=True)
