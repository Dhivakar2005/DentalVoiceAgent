"""verify_and_clear.py — Post-fix verification and pending cleanup."""
import os, sys, json
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from scheduling_automation.automation_engine import AutomationEngine, CUSTOMERS_SHEET, _load_pending, _save_pending
from datetime import datetime
from zoneinfo import ZoneInfo

engine = AutomationEngine()
now = datetime.now(ZoneInfo('Asia/Kolkata'))
today = now.date().isoformat()

# Check row 13 (Dental Crown / Apr-16 PREDICTED)
result = engine.service.spreadsheets().values().get(
    spreadsheetId=engine.spreadsheet_id,
    range=f'{CUSTOMERS_SHEET}!A13:K13'
).execute()
print('Row 13 (Dental Crown PREDICTED):')
for r in result.get('values', []):
    headers = ['CID','Name','Phone','Date','Time','Reason','Doctor','FutureDate','Type','Status','WA']
    for h, v in zip(headers, r):
        print(f'  {h} = {v}')

print()
print(f'Today (IST): {today}')
print(f'Apr 16 is: {"FUTURE" if today < "2026-04-16" else "TODAY or PAST"}')

# Clear the stale pending entry since its future_date 2026-04-16 is for Dental Crown
# which is a real future prediction - NOT the Smile Designing one we just fixed.
# The pending entry was written when the WA YES/NO request was sent for Dental Crown.
# If they didn't reply to that one yet, keep it. If the date is past, clear it.
pending = _load_pending()
print()
print('pending_replies.json:')
print(json.dumps(pending, indent=2))

# If Apr 16 is still future, the Dental Crown pending entry is still valid
# But if today >= Apr 16, it should be cleared
clean_phone = '8610080257'
if clean_phone in pending:
    ctx = pending[clean_phone]
    future_date = ctx.get('future_date', '')
    if future_date and today > future_date:
        print(f'\n[FIX] Dental Crown future_date {future_date} is in the PAST. Clearing stale pending entry.')
        del pending[clean_phone]
        _save_pending(pending)
        print('[FIX] Cleared.')
    else:
        print(f'\n[INFO] Dental Crown future_date {future_date} is still FUTURE. Keeping pending entry (valid YES/NO awaited).')
else:
    print('[INFO] No pending entry found - already cleared.')
