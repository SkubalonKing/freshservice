"""
----------------------------------------------------------------------
Script Name: contract_assets.py
Summary:
    This script performs the following tasks:
      1. Fetches all associated assets for a specified contract from the
         Freshservice API, handling pagination.
      2. For each asset, it retrieves additional details by making API calls:
         - Department Name from /api/v2/departments/[id]
         - Location Name from /api/v2/locations/[id]
         - Requester Name (combined first and last names) from /api/v2/requesters/[id]
      3. Writes the retrieved asset information to a CSV file. The CSV is named 
         using the format "YYYY-MM_Lease_1996594_Assets.csv" (where the date portion 
         is generated from the current date and the lease number is a constant).
      4. Updates a Freshservice ticket (supplied via a command-line argument)
         by attaching the generated CSV file. The ticket update is done via a PUT 
         request using a multipart/form-data payload, and it sets the ticket's priority 
         to 1.
      5. Upon successful ticket update (HTTP 200/201), deletes the CSV file from disk.
      
Usage:
    python contract_assets.py --ticket_id <ticket_id>
    For example:
    python contract_assets.py --ticket_id 54157

Configuration:
    - The Freshservice domain and API key are imported from the 'credentials' module.
    - The contract_id is set to 37 by default (modify if needed).
    - The lease_number variable is set to "1996594" and is used in the CSV filename.
    - The script requires the "requests" and "requests_toolbelt" modules.
      Install the requests_toolbelt module via:
          pip install requests-toolbelt

Dependencies:
    - Python 3.x
    - requests
    - requests_toolbelt
    - Standard library modules: os, io, csv, argparse, base64, datetime

Author: Sergio Gervacio
Date: 2025-04-08
---------------------------------------------------------------------- 
"""


import requests
import os
import io
import csv
import argparse
import base64
import credentials
import datetime
from requests_toolbelt.multipart.encoder import MultipartEncoder

# ---------------------------
# Parse command-line arguments
# ---------------------------
parser = argparse.ArgumentParser(description="Generate asset CSV and update a ticket with it.")
parser.add_argument("--ticket_id", type=int, required=True, help="The ticket ID to attach the CSV file to.")
args = parser.parse_args()
ticket_id = args.ticket_id

# ---------------------------
# Configuration for Freshservice
# ---------------------------
FSURL = credentials.fs_domain
contract_id = 37
api_key = credentials.fs_api_key  # Your Freshservice API key

# Ensure the base URL includes the scheme.
if not FSURL.startswith("http"):
    base_url = f"https://{FSURL}"
else:
    base_url = FSURL

# Set default headers for JSON API calls
json_headers = {
    "Content-Type": "application/json"
}

# ---------------------------
# Step 1: Fetch Assets and Generate CSV
# ---------------------------
all_assets = []
page = 1
per_page = 100  # Adjust if necessary

while True:
    asset_endpoint = f"{base_url}/api/v2/contracts/{contract_id}/associated-assets?page={page}&per_page={per_page}"
    response = requests.get(asset_endpoint, auth=(api_key, "X"), headers=json_headers)
    if response.status_code != 200:
        print(f"Error retrieving assets on page {page}: {response.status_code} {response.text}")
        break
    data = response.json()
    assets = data.get("associated_assets", [])
    if not assets:
        break
    all_assets.extend(assets)
    page += 1

# Set up caching to avoid duplicate API calls for additional details.
dept_cache = {}
location_cache = {}
requester_cache = {}

def get_department_name(dept_id):
    if not dept_id:
        return None
    if dept_id in dept_cache:
        return dept_cache[dept_id]
    dept_endpoint = f"{base_url}/api/v2/departments/{dept_id}"
    r = requests.get(dept_endpoint, auth=(api_key, "X"), headers=json_headers)
    if r.status_code == 200:
        dept_name = r.json().get("department", {}).get("name")
        dept_cache[dept_id] = dept_name
        return dept_name
    else:
        print(f"Error retrieving department {dept_id}: {r.status_code} {r.text}")
        return None

def get_location_name(loc_id):
    if not loc_id:
        return None
    if loc_id in location_cache:
        return location_cache[loc_id]
    loc_endpoint = f"{base_url}/api/v2/locations/{loc_id}"
    r = requests.get(loc_endpoint, auth=(api_key, "X"), headers=json_headers)
    if r.status_code == 200:
        loc_name = r.json().get("location", {}).get("name")
        location_cache[loc_id] = loc_name
        return loc_name
    else:
        print(f"Error retrieving location {loc_id}: {r.status_code} {r.text}")
        return None

def get_requester_name(user_id):
    if not user_id:
        return None
    if user_id in requester_cache:
        return requester_cache[user_id]
    requester_endpoint = f"{base_url}/api/v2/requesters/{user_id}"
    r = requests.get(requester_endpoint, auth=(api_key, "X"), headers=json_headers)
    if r.status_code == 200:
        requester = r.json().get("requester", {})
        first_name = requester.get("first_name", "").strip()
        last_name = requester.get("last_name", "").strip()
        requester_name = f"{first_name} {last_name}".strip() if first_name or last_name else None
        requester_cache[user_id] = requester_name
        return requester_name
    else:
        print(f"Error retrieving requester {user_id}: {r.status_code} {r.text}")
        return None

# Create the CSV file with asset, department, location, and requester data.
lease_number = "1996594"
csv_filename = f"{datetime.datetime.now().strftime('%Y-%m')}_Lease_{lease_number}_Assets.csv"
csv_file_path = csv_filename  # Use in place of "associated_assets.csv"
with open(csv_file_path, "w", newline="") as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(["display_id", "asset_tag", "name", "department", "location", "requester"])
    
    for asset in all_assets:
        display_id = asset.get("display_id")
        asset_tag = asset.get("asset_tag")
        asset_name = asset.get("name")
        
        dept_id = asset.get("department_id")
        loc_id = asset.get("location_id")
        user_id = asset.get("user_id")
        
        dept_name = get_department_name(dept_id) if dept_id else None
        loc_name = get_location_name(loc_id) if loc_id else None
        requester_name = get_requester_name(user_id) if user_id else None
        
        writer.writerow([display_id, asset_tag, asset_name, dept_name, loc_name, requester_name])

print(f"CSV file created successfully: {csv_file_path}")

# ---------------------------
# Step 2: Update the Ticket with the CSV as an Attachment
# ---------------------------
def update_ticket_with_attachment(ticket_id, file_path):
    """
    Updates the specified ticket by attaching the CSV file.
    This uses a PUT request to the ticket endpoint with a multipart/form-data payload.
    After a successful update, the CSV file is deleted.
    """
    ticket_endpoint = f"{base_url}/api/v2/tickets/{ticket_id}"
    
    # Read the entire file into memory and create a BytesIO stream.
    try:
        with open(file_path, "rb") as f:
            file_data = f.read()
        file_stream = io.BytesIO(file_data)
    except Exception as e:
        print(f"Error reading file {file_path}: {e}")
        return

    # Construct the fields for the multipart form-data.
    fields = {
        "priority": "1",  # field value as a string
        "attachments[]": (
            os.path.basename(file_path),
            file_stream,
            "application/octet-stream"
        )
    }
    
    # Use MultipartEncoder to build the body with proper boundaries.
    m = MultipartEncoder(fields=fields)
    
    # Prepare the Authorization header (using the API key).
    base64_auth = base64.b64encode(f"{api_key}:X".encode()).decode()
    headers = {
        "Authorization": f"Basic {base64_auth}",
        "Content-Type": m.content_type  # includes the boundary
    }
    
    
    try:
        response = requests.put(ticket_endpoint, data=m, headers=headers)
    except Exception as e:
        print("Exception during PUT request:", e)
        return
    
    # Debug: print the response details.
    print("Response status code:", response.status_code)
    
    if response.status_code in (200, 201):
        print(f"Ticket {ticket_id} updated successfully with the CSV attachment.")
        # Now that the file is no longer in use, delete it.
        try:
            os.remove(file_path)
            print(f"CSV file '{file_path}' deleted successfully.")
        except Exception as e:
            print(f"Error deleting CSV file '{file_path}':", e)
    else:
        print(f"Error updating ticket {ticket_id}: {response.status_code} {response.text}")

# IMPORTANT: Actually call the function to update the ticket.
print("Calling update_ticket_with_attachment()...")
update_ticket_with_attachment(ticket_id, csv_file_path)
