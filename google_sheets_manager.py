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
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)
            with open("token.pickle", "wb") as token:
                pickle.dump(creds, token)
        
        return build("sheets", "v4", credentials=creds)
    
    def initialize_sheet(self):
        """Create or find the customer database spreadsheet"""
        # Try to load existing spreadsheet ID from config file
        config_file = "sheets_config.json"
        if os.path.exists(config_file):
            try:
                import json
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    self.spreadsheet_id = config.get('spreadsheet_id')
                    if self.spreadsheet_id:
                        # Verify the spreadsheet still exists
                        try:
                            self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
                            print(f"✅ Using existing customer database: {self.spreadsheet_id}")
                            print(f"📊 View at: https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}")
                            return
                        except:
                            print("⚠️  Saved spreadsheet not found, creating new one...")
            except:
                pass
        
        # Create new spreadsheet if not found
        self.create_customer_sheet()
    
    def create_customer_sheet(self):
        """Create a new customer database spreadsheet"""
        spreadsheet = {
            'properties': {'title': SPREADSHEET_NAME},
            'sheets': [{
                'properties': {'title': self.sheet_name}
            }]
        }
        
        result = self.service.spreadsheets().create(body=spreadsheet).execute()
        self.spreadsheet_id = result['spreadsheetId']
        
        # Save spreadsheet ID to config file for reuse
        import json
        config_file = "sheets_config.json"
        with open(config_file, 'w') as f:
            json.dump({'spreadsheet_id': self.spreadsheet_id}, f)
        
        # Add headers
        headers = [['Customer ID', 'Name', 'Phone Number', 'First Created Date and Time', 'Appointment Date', 'Appointment Time', 'Appointment Reason']]
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f'{self.sheet_name}!A1:G1',
            valueInputOption='RAW',
            body={'values': headers}
        ).execute()
        
        print(f"✅ Created new customer database: {self.spreadsheet_id}")
        print(f"📊 View at: https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}")
        
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
            print(f"Error fetching sheet ID: {e}")
            return 0
    
    def generate_customer_id(self):
        """Generate next customer ID (CUST001, CUST002, etc.)"""
        try:
            # Get all customer IDs
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!A:A'
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
        """Retrieve latest customer details by customer ID from the log"""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!A:G'
            ).execute()
            
            values = result.get('values', [])
            
            # Search backwards to get the LATEST info for this ID
            for i in range(len(values) - 1, 0, -1):
                row = values[i]
                if row and len(row) > 0 and row[0].upper() == customer_id.upper():
                    return {
                        'customer_id': row[0],
                        'name': row[1] if len(row) > 1 else '',
                        'phone': row[2] if len(row) > 2 else '',
                        'created_date': row[3] if len(row) > 3 else '',
                        'appointment_date': row[4] if len(row) > 4 else '',
                        'appointment_time': row[5] if len(row) > 5 else '',
                        'appointment_reason': row[6] if len(row) > 6 else '',
                        'row_number': i + 1
                    }
            
            return None
        
        except Exception as e:
            print(f"Error getting customer by ID: {e}")
            return None
    
    def get_customer_by_name(self, name):
        """Search for customer by name (case-insensitive)"""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!A:G'
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
                        'appointment_date': row[4] if len(row) > 4 else '',
                        'appointment_time': row[5] if len(row) > 5 else '',
                        'appointment_reason': row[6] if len(row) > 6 else '',
                        'row_number': i
                    }
            
            return None
        
        except Exception as e:
            print(f"Error getting customer by name: {e}")
            return None
    
    def log_appointment(self, customer_id, name, phone, appointment_date, appointment_time, reason):
        """Log a NEW appointment row for any customer"""
        try:
            now = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
            values = [[customer_id, name, phone, now, appointment_date, appointment_time, reason]]
            
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!A:G',
                valueInputOption='RAW',
                insertDataOption='INSERT_ROWS',
                body={'values': values}
            ).execute()
            
            print(f"✅ Logged appointment for: {customer_id} - {name} on {appointment_date}")
            return True
        except Exception as e:
            print(f"Error logging appointment: {e}")
            return False

    def find_appointment_row(self, customer_id, date, time):
        """Find the row number for a specific appointment"""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!A:F'
            ).execute()
            values = result.get('values', [])
            
            for i, row in enumerate(values[1:], start=2):
                if (len(row) >= 6 and 
                    row[0].upper() == customer_id.upper() and 
                    row[4] == date and 
                    row[5] == time):
                    return i
            return None
        except Exception as e:
            print(f"Error finding appointment row: {e}")
            return None

    def update_appointment(self, customer_id, old_date, old_time, new_date, new_time):
        """Update a specific appointment row (for rescheduling)"""
        try:
            row_num = self.find_appointment_row(customer_id, old_date, old_time)
            if not row_num:
                return False
            
            # Update Date and Time columns (E and F)
            values = [[new_date, new_time]]
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!E{row_num}:F{row_num}',
                valueInputOption='RAW',
                body={'values': values}
            ).execute()
            
            print(f"✅ Updated appointment log for {customer_id}")
            return True
        except Exception as e:
            print(f"Error updating appointment log: {e}")
            return False

    def delete_appointment(self, customer_id, date, time):
        """Clear specific appointment fields (for cancellation) instead of deleting row"""
        try:
            row_num = self.find_appointment_row(customer_id, date, time)
            if not row_num:
                return False
            
            # Clear columns E, F, and G (Date, Time, Reason)
            # We use an empty list of lists with empty strings
            values = [["", "", ""]]
            
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!E{row_num}:G{row_num}',
                valueInputOption='RAW',
                body={'values': values}
            ).execute()
            
            print(f"✅ Cleared appointment details in row {row_num} for {customer_id}")
            return True
        except Exception as e:
            print(f"Error clearing appointment log: {e}")
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
        """Update existing customer information"""
        try:
            customer = self.get_customer_by_id(customer_id)
            if not customer:
                return False
            
            row_num = customer['row_number']
            
            # Update name if provided
            if name:
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f'{self.sheet_name}!B{row_num}',
                    valueInputOption='RAW',
                    body={'values': [[name]]}
                ).execute()
            
            # Update phone if provided
            if phone:
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f'{self.sheet_name}!C{row_num}',
                    valueInputOption='RAW',
                    body={'values': [[phone]]}
                ).execute()
            
            print(f"✅ Updated customer: {customer_id}")
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
                    range=f'{self.sheet_name}!E{row_num}',
                    valueInputOption='RAW',
                    body={'values': [[appointment_date]]}
                ).execute()
            
            # Update appointment time if provided
            if appointment_time:
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f'{self.sheet_name}!F{row_num}',
                    valueInputOption='RAW',
                    body={'values': [[appointment_time]]}
                ).execute()
            
            # Update reason if provided
            if reason:
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f'{self.sheet_name}!G{row_num}',
                    valueInputOption='RAW',
                    body={'values': [[reason]]}
                ).execute()
            
            return True
        
        except Exception as e:
            print(f"Error updating last visit: {e}")
            return False
    
    def get_all_customers(self):
        """Return all customer records"""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!A:G'
            ).execute()
            
            values = result.get('values', [])
            
            if len(values) <= 1:
                return []
            
            customers = []
            for row in values[1:]:  # Skip header
                if row and len(row) > 0:
                    customers.append({
                        'customer_id': row[0],
                        'name': row[1] if len(row) > 1 else '',
                        'phone': row[2] if len(row) > 2 else '',
                        'created_date': row[3] if len(row) > 3 else '',
                        'appointment_date': row[4] if len(row) > 4 else '',
                        'appointment_time': row[5] if len(row) > 5 else '',
                        'appointment_reason': row[6] if len(row) > 6 else ''
                    })
            
            return customers
        
        except Exception as e:
            print(f"Error getting all customers: {e}")
            return []
    def get_appointments_by_id(self, customer_id):
        """Return all appointments for a specific customer ID"""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.sheet_name}!A:G'
            ).execute()
            
            values = result.get('values', [])
            if len(values) <= 1:
                return []
            
            appointments = []
            customer_id_upper = customer_id.upper()
            
            for row in values[1:]:
                if row and len(row) > 0 and row[0].upper() == customer_id_upper:
                    appointments.append({
                        'customer_id': row[0],
                        'name': row[1] if len(row) > 1 else '',
                        'phone': row[2] if len(row) > 2 else '',
                        'created_date': row[3] if len(row) > 3 else '',
                        'appointment_date': row[4] if len(row) > 4 else '',
                        'appointment_time': row[5] if len(row) > 5 else '',
                        'appointment_reason': row[6] if len(row) > 6 else ''
                    })
            
            return appointments
        except Exception as e:
            print(f"Error getting appointments by ID: {e}")
            return []
