import os
from pymongo import MongoClient
from flask_bcrypt import Bcrypt
import re
import jwt
from datetime import datetime, timedelta
from thefuzz import fuzz, process

# MongoDB Configuration
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "dental_assistant"

class DatabaseManager:
    def __init__(self, app=None):
        self.client = MongoClient(MONGO_URI)
        self.db = self.client[DB_NAME]
        self.users = self.db["users"]
        self.bcrypt = Bcrypt(app) if app else Bcrypt()
        self.jwt_secret = os.environ.get("JWT_SECRET", "super-secret-smile-dental-key-2026")
        self.ensure_admin_exists()

    def ensure_admin_exists(self):
        admin_email = "admin@gmail.com"
        admin_password = "1111"
        if not self.users.find_one({"email": admin_email}):
            hashed_password = self.bcrypt.generate_password_hash(admin_password).decode('utf-8')
            self.users.insert_one({
                "email": admin_email,
                "password": hashed_password,
                "role": "admin",
                "name": "System Admin",
                "created_at": datetime.now()
            })
            print(f"[OK] Default admin user created: {admin_email}")

    def create_user(self, email, password, name, role="user"):
        if len(password) < 4: return False, "Password must be at least 4 characters"
        if self.users.find_one({"email": email}): return False, "Email already registered"
        hashed_password = self.bcrypt.generate_password_hash(password).decode('utf-8')
        user_data = {"email": email, "password": hashed_password, "name": name, "role": role, "created_at": datetime.now()}
        result = self.users.insert_one(user_data)
        return (True, "User registered successfully") if result.inserted_id else (False, "Registration failed")

    def authenticate_user(self, email, password):
        user = self.users.find_one({"email": email})
        if user and self.bcrypt.check_password_hash(user["password"], password): return user
        return None

    def generate_token(self, user_id, email, name, role):
        payload = {"user_id": str(user_id), "email": email, "name": name, "role": role, "exp": datetime.utcnow() + timedelta(hours=24)}
        return jwt.encode(payload, self.jwt_secret, algorithm="HS256")

    def decode_token(self, token):
        try: return jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
        except: return None

    def get_user_by_email(self, email):
        return self.users.find_one({"email": email})

    def create_customer(self, customer_id, name, phone, created_at=None):
        customer_id = customer_id.upper() if customer_id else ""
        customer_data = {"customer_id": customer_id, "name": name, "phone": phone, "created_at": created_at or datetime.now(), "updated_at": datetime.now()}
        self.db["customers"].update_one({"customer_id": customer_id}, {"$set": customer_data}, upsert=True)
        return True

    def get_customer_by_id(self, customer_id):
        return self.db["customers"].find_one({"customer_id": customer_id.upper()}) if customer_id else None

    def get_customer_by_name(self, name):
        return self.db["customers"].find_one({"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}) if name else None

    def get_customer_by_phone(self, phone):
        return self.db["customers"].find_one({"phone": phone}) if phone else None

    def find_customer_fuzzy(self, name, threshold=80):
        if not name: return None
        customers = list(self.db["customers"].find({}, {"name": 1, "customer_id": 1}))
        if not customers: return None
        name_to_customer = {c["name"]: c for c in customers}
        best_match, score = process.extractOne(name, list(name_to_customer.keys()), scorer=fuzz.WRatio)
        return self.get_customer_by_id(name_to_customer[best_match]["customer_id"]) if score >= threshold else None

    def update_customer(self, customer_id, name=None, phone=None):
        if not customer_id: return False
        upd = {"updated_at": datetime.now()}
        if name: upd["name"] = name
        if phone: upd["phone"] = phone
        return self.db["customers"].update_one({"customer_id": customer_id.upper()}, {"$set": upd}).modified_count > 0

    def get_next_customer_id(self):
        customers = list(self.db["customers"].find({}, {"customer_id": 1}))
        if not customers: return "CUST001"
        max_num = 0
        for c in customers:
            cid = c.get("customer_id", "")
            if cid.startswith("CUST"):
                try:
                    num = int(cid.replace("CUST", "")); max_num = max(max_num, num)
                except: continue
        return f"CUST{max_num + 1:03d}"

    def get_all_customers_data(self):
        return list(self.db["customers"].find())

    # --- Session Management ---
    def get_session_state(self, session_id):
        if not session_id: return None
        res = self.db["sessions"].find_one({"session_id": session_id})
        return res.get("state") if res else None

    def update_session_state(self, session_id, state):
        if not session_id: return
        self.db["sessions"].update_one(
            {"session_id": session_id},
            {"$set": {"session_id": session_id, "state": state, "updated_at": datetime.now()}},
            upsert=True
        )