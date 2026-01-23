import os
from pymongo import MongoClient
from flask_bcrypt import Bcrypt
from datetime import datetime

# MongoDB Configuration
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "dental_assistant"

class DatabaseManager:
    def __init__(self, app=None):
        self.client = MongoClient(MONGO_URI)
        self.db = self.client[DB_NAME]
        self.users = self.db["users"]
        self.bcrypt = Bcrypt(app) if app else Bcrypt()
        
        # Ensure default admin exists
        self.ensure_admin_exists()

    def ensure_admin_exists(self):
        """Create the default admin user if it doesn't exist"""
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
            print(f"✅ Default admin user created: {admin_email}")

    def create_user(self, email, password, name, role="user"):
        """Register a new user"""
        if self.users.find_one({"email": email}):
            return False, "Email already registered"
        
        hashed_password = self.bcrypt.generate_password_hash(password).decode('utf-8')
        user_data = {
            "email": email,
            "password": hashed_password,
            "name": name,
            "role": role,
            "created_at": datetime.now()
        }
        
        result = self.users.insert_one(user_data)
        if result.inserted_id:
            return True, "User registered successfully"
        return False, "Registration failed"

    def authenticate_user(self, email, password):
        """Verify user credentials"""
        user = self.users.find_one({"email": email})
        if user and self.bcrypt.check_password_hash(user["password"], password):
            return user
        return None

    def get_user_by_email(self, email):
        return self.users.find_one({"email": email})
