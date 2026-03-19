import os
import pickle
from datetime import datetime
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request


# Configuration
SCOPES = [

    "https://www.googleapis.com/auth/calendar",

    "https://www.googleapis.com/auth/spreadsheets"

]

TIMEZONE = "Asia/Kolkata"
SPREADSHEET_NAME = "Dental_Customer_Database"
CUSTOMER_MASTER_SHEET = "Customer_Master"

class GoogleSheetsManager:

    """Manages customer data in Google Sheets"""
    def __init__(self):

        self.service = self.authenticate()

        self.spreadsheet_id = None

        self.sheet_name = "Customers"

        self.initialize_sheet()

    
    def authenticate(self):
        
        """Authenticate with Google Sheets API"""
        creds = None
        try:
            with open("token.pickle", "rb") as token:
                creds = pickle.load(token)
        except FileNotFoundError:
            pass

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                import time
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        creds.refresh(Request())
                        break
                    except Exception as e:
                        if attempt == max_retries - 1:
                            print(f"[ERROR] Error refreshing Google Sheets API credentials: {e}")
                            print("[WARNING] Please delete 'token.pickle' and restart the server to re-authorize.")
                            raise e
                        print(f"[WARNING] Google Sheets refresh failed (attempt {attempt+1}/{max_retries}). Retrying in 2s...")
                        time.sleep(2)
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)
            with open("token.pickle", "wb") as token:
                pickle.dump(creds, token)
        return build("sheets", "v4", credentials=creds)

    def initialize_sheet(self):

        """Create or find the customer database spreadsheet and ensure required sheets exist"""
        config_file = "sheets_config.json"
        if os.path.exists(config_file):
            try:
                import json
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    self.spreadsheet_id = config.get('spreadsheet_id')
                    if self.spreadsheet_id:
                        try:
                            # Get spreadsheet metadata
                            spreadsheet = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
                            existing_sheets = [s['properties']['title'] for s in spreadsheet.get('sheets', [])]
                            # Check and add Customer_Master if missing
                            if CUSTOMER_MASTER_SHEET not in existing_sheets:
                                body = {'requests': [{'addSheet': {'properties': {'title': CUSTOMER_MASTER_SHEET}}}]}
                                self.service.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body=body).execute()
                                 # Add headers

                                headers = [['Customer ID', 'Name', 'Phone Number', 'First Created Date and Time']]
                                self.service.spreadsheets().values().update(
                                    spreadsheetId=self.spreadsheet_id,
                                    range=f'{CUSTOMER_MASTER_SHEET}!A1:D1',
                                    valueInputOption='RAW',
                                    body={'values': headers}
                                ).execute()
                                print(f"[OK] Added missing sheet: {CUSTOMER_MASTER_SHEET}")
                            # Check and add Customers if missing
                            if self.sheet_name not in existing_sheets:
                                body = {'requests': [{'addSheet': {'properties': {'title': self.sheet_name}}}]}
                                self.service.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body=body).execute()
                                # Add headers (No timestamp for Customers)
                                headers = [['Customer ID', 'Name', 'Phone Number', 'Appointment Date', 'Appointment Time', 'Appointment Reason']]
                                self.service.spreadsheets().values().update(
                                    spreadsheetId=self.spreadsheet_id,
                                    range=f'{self.sheet_name}!A1:F1',
                                    valueInputOption='RAW',
                                    body={'values': headers}
                                ).execute()
                                print(f"[OK] Added missing sheet: {self.sheet_name}")
                            print(f"[OK] Using customer database: {self.spreadsheet_id}")
                            return
                        except Exception as e:
                            print(f"[ERROR] Error accessing configured spreadsheet: {e}")
                            print("[WARNING] Please check your internet connection or 'sheets_config.json'.")
                            print("[INFO] To force a new sheet creation, delete 'sheets_config.json'.")
                            self.spreadsheet_id = config.get('spreadsheet_id') 
                            return
            except Exception as e:
                print(f"Error reading config: {e}")
        # Only create new if config didn't exist
        self.create_customer_sheet()

    
    def create_customer_sheet(self):

        """Create a new customer database spreadsheet with two sheets"""
        spreadsheet = {
            'properties': {'title': SPREADSHEET_NAME},
            'sheets': [
                {
                    'properties': {'title': CUSTOMER_MASTER_SHEET}
                },
                {
                    'properties': {'title': self.sheet_name}
                }
            ]
        }
        result = self.service.spreadsheets().create(body=spreadsheet).execute()
        self.spreadsheet_id = result['spreadsheetId']
        # Save spreadsheet ID to config file for reuse
        import json
        config_file = "sheets_config.json"
        with open(config_file, 'w') as f:
            json.dump({'spreadsheet_id': self.spreadsheet_id}, f)
        # Add headers to Customer_Master sheet
        master_headers = [['Customer ID', 'Name', 'Phone Number', 'First Created Date and Time']]
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f'{CUSTOMER_MASTER_SHEET}!A1:D1',
            valueInputOption='RAW',
            body={'values': master_headers}
        ).execute()
        # Add headers to Customers (appointment log) sheet - No timestamp
        appointment_headers = [['Customer ID', 'Name', 'Phone Number', 'Appointment Date', 'Appointment Time', 'Appointment Reason']]
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f'{self.sheet_name}!A1:F1',
            valueInputOption='RAW',
            body={'values': appointment_headers}
        ).execute()
        print(f"[OK] Created new customer database: {self.spreadsheet_id}")
        print(f"[INFO] View at: https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}")

    def get_sheet_id(self):

        """Fetch the actual GID for the 'Customers' sheet from spreadsheet metadata"""
        try:
            spreadsheet = self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id
            ).execute()

            for sheet in spreadsheet.get('sheets', []):
                if sheet.get('properties', {}).get('title') == self.sheet_name:
                    return sheet.get('properties', {}).get('sheetId')
            # Fallback to 0 if not found (though unusual)
            return 0
        except Exception as e:
            print(f"[ERROR] Error fetching sheet ID: {e}")
            return 0

    def generate_customer_id(self):

        """Generate next customer ID (CUST001, CUST002, etc.) from Customer_Master sheet"""
        try:
            # Get all customer IDs from Customer_Master sheet
            result = self.service.spreadsheets().values().get(

                spreadsheetId=self.spreadsheet_id,

                range=f'{CUSTOMER_MASTER_SHEET}!A:A'

            ).execute()

            values = result.get('values', [])
            if len(values) <= 1:  # Only header or empty
                return "CUST001"
            # Extract numbers from existing IDs and find max
            max_num = 0
            for row in values[1:]:  # Skip header
                if row and row[0].startswith('CUST'):
                    try:
                        num = int(row[0].replace('CUST', ''))
                        max_num = max(max_num, num)
                    except:
                        continue
            # Generate next ID
            next_num = max_num + 1
            return f"CUST{next_num:03d}"
        except Exception as e:
            print(f"Error generating customer ID: {e}")
            return "CUST001"

    def get_customer_by_id(self, customer_id):

        """Retrieve customer details by customer ID from the Master sheet"""
        try:
            result = self.service.spreadsheets().values().get(

                spreadsheetId=self.spreadsheet_id,

                range=f'{CUSTOMER_MASTER_SHEET}!A:D'

            ).execute()

            values = result.get('values', [])
            # Simple search in Master
            for i, row in enumerate(values[1:], start=2):
                if row and len(row) > 0 and row[0].upper() == customer_id.upper():
                    return {

                        'customer_id': row[0],

                        'name': row[1] if len(row) > 1 else '',

                        'phone': row[2] if len(row) > 2 else '',

                        'created_date': row[3] if len(row) > 3 else '',

                        'row_number': i

                    }
            return None
        except Exception as e:
            print(f"Error getting customer by ID: {e}")
            return None

    def get_customer_by_name(self, name):

        """Search for customer by name in the Master sheet"""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,

                range=f'{CUSTOMER_MASTER_SHEET}!A:D'

            ).execute()

            values = result.get('values', [])
            name_lower = name.lower().strip()
            for i, row in enumerate(values[1:], start=2):
                if len(row) > 1 and row[1].lower().strip() == name_lower:
                    return {

                        'customer_id': row[0],

                        'name': row[1],

                        'phone': row[2] if len(row) > 2 else '',

                        'created_date': row[3] if len(row) > 3 else '',

                        'row_number': i

                    }
            return None
        except Exception as e:
            print(f"Error getting customer by name: {e}")
            return None

    def customer_exists_in_master(self, customer_id):

        """Check if customer ID exists in Customer_Master sheet"""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,

                range=f'{CUSTOMER_MASTER_SHEET}!A:A'

            ).execute()

            values = result.get('values', [])
            customer_id_upper = customer_id.upper()
            for row in values[1:]:  # Skip header
                if row and row[0].upper() == customer_id_upper:
                    return True
            return False
        except Exception as e:
            print(f"Error checking customer in master: {e}")
            return False

    def log_new_customer(self, customer_id, name, phone):

        """Log a new customer to Customer_Master sheet"""
        try:
            now = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
            values = [[customer_id, name, phone, now]]
            self.service.spreadsheets().values().append(

                spreadsheetId=self.spreadsheet_id,

                range=f'{CUSTOMER_MASTER_SHEET}!A:D',

                valueInputOption='RAW',

                insertDataOption='INSERT_ROWS',

                body={'values': values}

            ).execute()

            print(f"[OK] Logged new customer to Master: {customer_id} - {name}")
            return True
        except Exception as e:
            print(f"Error logging new customer to master: {e}")
            return False

    def _load_offline_data(self):

        """Load data from offline storage"""
        import json
        if os.path.exists("offline_appointments.json"):
            try:
                with open("offline_appointments.json", "r") as f:
                    return json.load(f)
            except:
                return []
        return []

    def _save_offline_data(self, data):

        """Save data to offline storage"""
        import json
        with open("offline_appointments.json", "w") as f:
            json.dump(data, f, indent=2)



    def log_appointment(self, customer_id, name, phone, appointment_date, appointment_time, reason):

        """Log a NEW appointment row. If Sheets fails, save offline."""
        try:
            # Check online status / functionality first by trying to access master
            # If this logic is too brittle, we can just wrap the whole thing
            # Check if customer exists in Customer_Master, if not add them
            if not self.customer_exists_in_master(customer_id):
                self.log_new_customer(customer_id, name, phone)
            values = [[customer_id, name, phone, appointment_date, appointment_time, reason]]
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!A:F',
                valueInputOption='RAW',
                insertDataOption='INSERT_ROWS',
                body={'values': values}
            ).execute()
            print(f"[OK] Logged appointment for: {customer_id} - {name} on {appointment_date}")
            # Since online worked, try to sync any pending offline data
            self.sync_offline_data()
            return True
        except Exception as e:
            print(f"[WARNING] Error logging appointment to Sheets: {e}")
            print(f"[SAVE] Saving to offline storage for later sync...")
            offline_data = self._load_offline_data()
            offline_data.append({
                "type": "appointment",
                "customer_id": customer_id,
                "name": name,
                "phone": phone,
                "appointment_date": appointment_date,
                "appointment_time": appointment_time,
                "reason": reason,
                "timestamp": datetime.now().isoformat()
            })
            self._save_offline_data(offline_data)
            return True # Return True so the app thinks it succeeded

    def get_all_customers(self):

        """Return all customer records (Online + Offline)"""
        customers = []
        # 1. Try to fetch from Sheets
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!A:F'
            ).execute()
            values = result.get('values', [])
            if len(values) > 1:
                for row in values[1:]:  # Skip header
                    if isinstance(row, list) and len(row) >= 5:
                        customers.append({
                            'customer_id': row[0],

                            'name': row[1] if len(row) > 1 else '',

                            'phone': row[2] if len(row) > 2 else '',

                            'appointment_date': row[3] if len(row) > 3 else '',

                            'appointment_time': row[4] if len(row) > 4 else '',

                            'appointment_reason': row[5] if len(row) > 5 else '',

                            'source': 'online'
                        })

        except Exception as e:
            print(f"[WARNING] Could not fetch from Sheets: {e}")       
        # 2. Merge offline data
        offline_data = self._load_offline_data()
        for item in offline_data:
            if item.get("type") == "appointment":
                customers.append({

                    'customer_id': item["customer_id"],

                    'name': item["name"],

                    'phone': item["phone"],

                    'appointment_date': item["appointment_date"],

                    'appointment_time': item["appointment_time"],

                    'appointment_reason': item["reason"],

                    'source': 'offline_pending' # Flag so admin knows it's pending

                })
        return customers

    def sync_offline_data(self):

        """Try to upload offline data to Sheets"""
        offline_data = self._load_offline_data()

        if not offline_data:
            return
        print(f"[SYNC] Attempting to sync {len(offline_data)} offline records...")
        remaining_data = []

        for item in offline_data:
            try:
                if item["type"] == "appointment":
                    # We use the internal calls but need to avoid infinite recursion of offline saving
                    # So we use the raw API calls here or a flag
                    # 1. Ensure master record
                    # We can reuse log_new_customer logic but wrapped
                    if not self.customer_exists_in_master(item["customer_id"]):
                        self.log_new_customer(item["customer_id"], item["name"], item["phone"])
                    # 2. Log appointment
                    values = [[item["customer_id"], item["name"], item["phone"], item["appointment_date"], item["appointment_time"], item["reason"]]]
                    self.service.spreadsheets().values().append(

                        spreadsheetId=self.spreadsheet_id,

                        range=f'{self.sheet_name}!A:F',

                        valueInputOption='RAW',

                        insertDataOption='INSERT_ROWS',

                        body={'values': values}

                    ).execute()
                    print(f"[OK] Synced: {item['customer_id']}")
            except Exception as e:
                print(f"[ERROR] Sync failed for {item.get('customer_id')}: {e}")
                remaining_data.append(item) # Keep for next time
        self._save_offline_data(remaining_data)
        if not remaining_data:
            print("[OK] All offline data synced successfully!")



    def find_appointment_row(self, customer_id, date, time, name=None):

        """Find the row number for a specific appointment with robust matching"""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!A:F'
            ).execute()
            values = result.get('values', [])
            if not values or len(values) <= 1:
                print("[WARNING] [Sheets] Sheet is empty or only has headers.")
                return None

            # Normalize inputs
            search_id = str(customer_id).strip().upper() if customer_id else ""
            search_date = str(date).strip()
            search_time = str(time).strip().upper()
            search_name = str(name).strip().upper() if name else None

            print(f"[SEARCH] [Sheets] Searching for: ID={search_id}, Date={search_date}, Time={search_time}, Name={search_name}")

            for i, row in enumerate(values[1:], start=2):
                if len(row) < 5:
                    continue
                # Extract row values
                row_id = str(row[0]).strip().upper()
                row_name = str(row[1]).strip().upper()
                row_date = str(row[3]).strip()
                row_time = str(row[4]).strip().upper()
                # Basic match (ID, Date, Time)
                id_match = (row_id == search_id)
                date_match = (row_date == search_date)
                time_match = (row_time == search_time)
                # Optional name match if provided
                name_match = True
                if search_name:
                    name_match = (row_name == search_name)
                if id_match and date_match and time_match and name_match
                    print(f"[OK] [Sheets] Found matching row at index {i}")
                    return i
            # FALLBACK: If time match failed, check if there's ONLY ONE row for this ID and Date
            possible_rows: list[int] = []
            for i, row in enumerate(values[1:], start=2):
                if len(row) < 4: continue
                if str(row[0]).strip().upper() == search_id and str(row[3]).strip() == search_date:
                    possible_rows.append(i)

            if len(possible_rows) == 1:
                print(f"[WARNING] [Sheets] Exact time match failed, but found single row for {search_id} on {search_date} at index {possible_rows[0]}")
                return possible_rows[0]

            print(f"[ERROR] [Sheets] No exact match found for {search_id} on {search_date} at {search_time}")
            return None
        except Exception as e:
            print(f"[ERROR] [Sheets] Error finding appointment row: {e}")
            return None

    def update_appointment(self, customer_id, old_date, old_time, new_date, new_time, name=None):

        """Update a specific appointment row (for rescheduling)"""
        try:
            row_num = self.find_appointment_row(customer_id, old_date, old_time, name=name)
            if not row_num:
                # If name was provided, try one more time without name if that failed
                if name:
                    print(f"[WARNING] [Sheets] Search with Name failed, retrying without name...")
                    row_num = self.find_appointment_row(customer_id, old_date, old_time)
                if not row_num:
                    print(f"[ERROR] [Sheets] Could not find row to update for {customer_id}")
                    return False

            # Update Date and Time columns (D and E)
            values = [[str(new_date).strip(), str(new_time).strip().upper()]]
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,

                range=f'{self.sheet_name}!D{row_num}:E{row_num}',

                valueInputOption='RAW',

                body={'values': values}

            ).execute()

            print(f"[OK] [Sheets] Successfully updated appointment row {row_num} for {customer_id}")
            return True
        except Exception as e:
            print(f"[ERROR] [Sheets] Error updating appointment log: {e}")
            return False

    def delete_appointment(self, customer_id, date, time, name=None):

        """Delete entire appointment row (for cancellation)"""
        try:
            row_num = self.find_appointment_row(customer_id, date, time, name=name)
            if not row_num:
                # If name was provided, try one more time without name if that failed
                if name:
                    print(f"[WARNING] [Sheets] Search with Name failed, retrying without name...")
                    row_num = self.find_appointment_row(customer_id, date, time)
                if not row_num:
                    print(f"[ERROR] [Sheets] Could not find row to delete for {customer_id}")
                    return False
            # Get the sheet ID for the Customers sheet
            sheet_id = self.get_sheet_id()
            # Delete the entire row using batchUpdate
            # Row index is 0-based, so subtract 1 from row_num
            requests = [{
                'deleteDimension': {
                    'range': {
                        'sheetId': sheet_id,

                        'dimension': 'ROWS',

                        'startIndex': row_num - 1,

                        'endIndex': row_num
                    }
                }
            }]
            body = {'requests': requests}
            self.service.spreadsheets().batchUpdate(

                spreadsheetId=self.spreadsheet_id,

                body=body

            ).execute()

            print(f"[OK] Deleted appointment row {row_num} for {customer_id}")
            return True
        except Exception as e:
            print(f"Error deleting appointment row: {e}")
            return False

    def create_customer(self, name, phone, appointment_date='', appointment_time='', reason=''):

        """Backward compatibility: Just logs an appointment with a new ID"""
        try:
            customer_id = self.generate_customer_id()
            self.log_appointment(customer_id, name, phone, appointment_date, appointment_time, reason)
            return customer_id
        except Exception as e:
            print(f"Error creating customer: {e}")
            return None

    def update_customer(self, customer_id, name=None, phone=None):

        """Update existing customer information in Customer_Master sheet.
        NOTE: Customer ID is PERMANENT and CANNOT be changed."""
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return False
            row_num = customer['row_number']

            # Update name if provided (Column B in Customer_Master)
            if name:
                self.service.spreadsheets().values().update(

                    spreadsheetId=self.spreadsheet_id,

                    range=f'{CUSTOMER_MASTER_SHEET}!B{row_num}',

                    valueInputOption='RAW',

                    body={'values': [[name]]}

                ).execute()

            # Update phone if provided (Column C in Customer_Master)
            if phone:
                self.service.spreadsheets().values().update(

                    spreadsheetId=self.spreadsheet_id,

                    range=f'{CUSTOMER_MASTER_SHEET}!C{row_num}',

                    valueInputOption='RAW',

                    body={'values': [[phone]]}

                ).execute()

            print(f"[OK] Updated customer in Master: {customer_id}")
            return True
        except Exception as e:
            print(f"Error updating customer: {e}")
            return False

    def update_last_visit(self, customer_id, appointment_date='', appointment_time='', reason=''):

        """Update the appointment date, time, and reason for a customer"""
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return False
            row_num = customer['row_number']

            # Update appointment date if provided
            if appointment_date:
                self.service.spreadsheets().values().update(

                    spreadsheetId=self.spreadsheet_id,

                    range=f'{self.sheet_name}!D{row_num}',

                    valueInputOption='RAW',

                    body={'values': [[appointment_date]]}

                ).execute()

            # Update appointment time if provided
            if appointment_time:
                self.service.spreadsheets().values().update(

                    spreadsheetId=self.spreadsheet_id,

                    range=f'{self.sheet_name}!E{row_num}',

                    valueInputOption='RAW',

                    body={'values': [[appointment_time]]}

                ).execute()

            # Update reason if provided
            if reason:
                self.service.spreadsheets().values().update(

                    spreadsheetId=self.spreadsheet_id,

                    range=f'{self.sheet_name}!F{row_num}',

                    valueInputOption='RAW',

                    body={'values': [[reason]]}

                ).execute()
            return True
        except Exception as e:
            print(f"Error updating last visit: {e}")
            return False



    def get_appointments_by_id(self, customer_id):

        """Return all appointments for a specific customer ID"""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!A:F'
            ).execute()
            values = result.get('values', [])
            if len(values) <= 1:
                return []
            appointments = []
            customer_id_upper = customer_id.upper()
            for row in values[1:]:
                if isinstance(row, list) and len(row) >= 5 and row[0].upper() == customer_id_upper:
                    appointments.append({

                        'customer_id': row[0],

                        'name': row[1] if len(row) > 1 else '',

                        'phone': row[2] if len(row) > 2 else '',

                        'appointment_date': row[3] if len(row) > 3 else '',

                        'appointment_time': row[4] if len(row) > 4 else '',

                        'appointment_reason': row[5] if len(row) > 5 else ''

                    })
            return appointments
        except Exception as e:
            print(f"Error getting appointments by ID: {e}")
            return []

    def seed_requested_data(self):

        """Seed the specific data requested by the user into Customer_Master"""
        try:
            # Data: CUST001, Dhivakar G, 8610080257, 2026-01-22 22:08:36
            customer_id = "CUST001"
            name = "Dhivakar G"
            phone = "8610080257"
            timestamp = "2026-01-22 22:08:36"

            if not self.customer_exists_in_master(customer_id):
                values = [[customer_id, name, phone, timestamp]]
                self.service.spreadsheets().values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range=f'{CUSTOMER_MASTER_SHEET}!A:D',
                    valueInputOption='RAW',
                    insertDataOption='INSERT_ROWS',
                    body={'values': values}
                ).execute()
                print(f"[OK] Seeded {customer_id} data into Customer_Master")
            else:
                print(f"[INFO] {customer_id} already exists in Master, skipping seed.")
            return True
        except Exception as e:
            print(f"Error seeding data: {e}")
            return False

if __name__ == "__main__":

    # Maintenance / Seeding entry point
    gsm = GoogleSheetsManager()
    # Try to sync on startup
    gsm.sync_offline_data()
    gsm.seed_requested_data()