"""
fix_cust001_yes.py

One-time fix for CUST001 (Dhivakar G, 8610080257):
  - Patient sent YES with time "3:00 PM" but the appointment was not booked in the sheet.
  - pending_replies.json has an OUTDATED future_date (2026-04-16).
  - Per the user, the actual predicted future date should be 2026-04-23.

This script:
  1. Reads the sheet to find the real PREDICTED row for CUST001
  2. Confirms it (Type=BOOKED, Status=CONFIRMED, Time=3:00 PM)
  3. Normalizes Col D=future_date, Col H=N/A
  4. Sends the missing WhatsApp YES confirmation message
  5. Clears the pending_replies.json entry
"""

import os
import sys
import json
import pickle
import structlog

# Ensure root project is on path
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from scheduling_automation.automation_engine import AutomationEngine, _load_pending, _save_pending
from scheduling_automation.whatsapp_service import send_yes_confirmation

def find_predicted_rows_for_cust(engine, cid):
    """Scan the sheet and find all PREDICTED rows for a given CID."""
    from scheduling_automation.automation_engine import CUSTOMERS_SHEET
    try:
        result = engine.service.spreadsheets().values().get(
            spreadsheetId=engine.spreadsheet_id,
            range=f"{CUSTOMERS_SHEET}!A:K"
        ).execute()
        rows = result.get("values", [])
        found = []
        for i, r in enumerate(rows[1:], start=2):
            if len(r) >= 9 and str(r[0]).strip() == cid:
                found.append({
                    "row_idx": i,
                    "CID": r[0],
                    "Name": r[1],
                    "Phone": r[2],
                    "Col_D_OrigDate": r[3],
                    "Col_E_Time": r[4] if len(r) > 4 else "",
                    "Col_F_Reason": r[5] if len(r) > 5 else "",
                    "Col_G_Doctor": r[6] if len(r) > 6 else "",
                    "Col_H_FutureDate": r[7] if len(r) > 7 else "",
                    "Col_I_Type": r[8],
                    "Col_J_Status": r[9] if len(r) > 9 else "",
                    "Col_K_WA": r[10] if len(r) > 10 else "",
                })
        return found
    except Exception as e:
        print(f"Error reading sheet: {e}")
        return []

def main():
    print("=" * 60)
    print("CUST001 YES Reply Fix Script")
    print("=" * 60)

    engine = AutomationEngine()

    CID         = "CUST001"
    PHONE       = "+918610080257"
    CLEAN_PHONE = "8610080257"
    NAME        = "Dhivakar G"
    REASON      = "Smile Designing"   # Per user description
    USER_TIME   = "3:00 PM"

    # Step 1: Show all CUST001 rows for diagnosis
    print(f"\n[FIX] Scanning sheet for all rows with CID={CID}...")
    all_rows = find_predicted_rows_for_cust(engine, CID)
    
    if not all_rows:
        print("[FIX] ERROR: No rows found for CUST001 in the sheet!")
        return

    print(f"\n[FIX] Found {len(all_rows)} row(s) for CUST001:")
    for r in all_rows:
        print(f"  Row {r['row_idx']}: Type={r['Col_I_Type']}, Status={r['Col_J_Status']}, "
              f"Date(D)={r['Col_D_OrigDate']}, FutureDate(H)={r['Col_H_FutureDate']}, "
              f"Reason={r['Col_F_Reason']}, Time(E)={r['Col_E_Time']}, WA={r['Col_K_WA']}")

    # Step 2: Find the PREDICTED/PENDING row to confirm
    target_row = None
    for r in all_rows:
        if r["Col_I_Type"] == "PREDICTED" and r["Col_J_Status"] == "PENDING":
            target_row = r
            break

    if not target_row:
        print("\n[FIX] No PREDICTED/PENDING row found for CUST001.")
        print("      If the row is already BOOKED/CONFIRMED, the YES was processed.")
        print("      Clearing pending_replies.json entry only.")
        # Just clear the pending entry
        pending = _load_pending()
        if CLEAN_PHONE in pending:
            del pending[CLEAN_PHONE]
            _save_pending(pending)
            print("[FIX] Cleared pending_replies.json entry.")
        return

    actual_future_date = target_row["Col_H_FutureDate"]
    row_idx            = target_row["row_idx"]
    
    print(f"\n[FIX] Target row: {row_idx} | FutureDate={actual_future_date} | Reason={target_row['Col_F_Reason']}")
    print(f"[FIX] Confirming with Time={USER_TIME}...")

    from scheduling_automation.automation_engine import CUSTOMERS_SHEET

    try:
        # Step 1: Set Type=BOOKED, Status=CONFIRMED, WA=PENDING
        engine.service.spreadsheets().values().update(
            spreadsheetId=engine.spreadsheet_id,
            range=f"{CUSTOMERS_SHEET}!I{row_idx}:K{row_idx}",
            valueInputOption="RAW",
            body={"values": [["BOOKED", "CONFIRMED", "PENDING"]]}
        ).execute()
        print(f"[FIX] Set I=BOOKED, J=CONFIRMED, K=PENDING for row {row_idx}")

        # Step 2: Move future_date -> Col D, USER_TIME -> Col E
        engine.service.spreadsheets().values().update(
            spreadsheetId=engine.spreadsheet_id,
            range=f"{CUSTOMERS_SHEET}!D{row_idx}:E{row_idx}",
            valueInputOption="RAW",
            body={"values": [[actual_future_date, USER_TIME]]}
        ).execute()
        print(f"[FIX] Set D={actual_future_date}, E={USER_TIME} for row {row_idx}")

        # Step 3: Clear Col H -> N/A
        engine.service.spreadsheets().values().update(
            spreadsheetId=engine.spreadsheet_id,
            range=f"{CUSTOMERS_SHEET}!H{row_idx}",
            valueInputOption="RAW",
            body={"values": [["N/A"]]}
        ).execute()
        print(f"[FIX] Set H=N/A for row {row_idx}")

    except Exception as e:
        print(f"[FIX] ERROR updating sheet: {e}")
        return

    # Step 4: Send missing WhatsApp YES confirmation
    print(f"\n[FIX] Sending missing YES confirmation WhatsApp to {PHONE} ...")
    reason_to_use = target_row["Col_F_Reason"] or REASON
    ok = send_yes_confirmation(PHONE, NAME, actual_future_date, USER_TIME, reason_to_use, lang="en")
    if ok:
        print("[FIX] WhatsApp YES confirmation SENT successfully!")
    else:
        print("[FIX] ERROR: WhatsApp send failed. Check WA credentials.")

    # Step 5: Clear pending_replies.json
    pending = _load_pending()
    if CLEAN_PHONE in pending:
        del pending[CLEAN_PHONE]
        _save_pending(pending)
        print("[FIX] Cleared pending_replies.json entry.")

    # Step 6: Update state store
    pred_key = f"PRED_{CID}_{actual_future_date}"
    engine.state.set_prediction_status(pred_key, "CONFIRMED")
    print(f"[FIX] State updated: {pred_key} = CONFIRMED")

    print("\n[FIX] Done! Summary:")
    print(f"  - Row {row_idx} in sheet: BOOKED/CONFIRMED | Date={actual_future_date} | Time={USER_TIME}")
    print(f"  - WhatsApp sent: {'YES' if ok else 'FAILED'}")
    print(f"  - pending_replies.json: cleared")
    print("=" * 60)

if __name__ == "__main__":
    main()
