"""
----------------------------------------------------------------------
Script Name: freshservice_hrms_termination.py

Summary:
    Automates end‑of‑employment processing across Freshservice and HRMS:
      1. Accepts a Freshservice ticket ID from the command line.
      2. Pulls the ticket and extracts the **Employee ID** from its description
         (expects the string “Employee ID: <number>”).
      3. Logs in to **HRMS**, runs a saved report, and retrieves the
         employee’s record, normalising dates (MM/DD/YYYY ➜ YYYY‑MM‑DD).
      4. Searches Freshservice for the employee’s email—first among Requesters,
         then Agents—to obtain the requester ID and (optionally) their
         manager ID/email.
      5. Creates a Service Catalog request (item 95) in Freshservice to trigger
         the IT off‑boarding workflow, pre-filling:
             • date_of_change          (IT Deactivation Date)
             • employee_change_type    (“Termination”)
             • untitled                (employee reference/requester ID)

Usage:
    python freshservice_hrms_termination.py <freshservice_ticket_id>
    Example:
    python freshservice_hrms_termination.py 54157

Configuration:
    • All secrets live in **credentials.py**:
        - fs_api_key, fs_password, fs_domain      # Freshservice
        - apiKey, username, password, company     # HRMS
    • Service Catalog item ID is hard‑coded as 95 (change if needed).
    • Falls back to “noreply@hrms.com” when the manager’s email is missing.
    • A 5-second sleep after HRMS login mitigates occasional auth latency.

Dependencies:
    • Python 3.9+
    • requests
    • xmltodict
    • Standard library: argparse, re, base64, json, time, datetime

Author: Sergio Gervacio
Date: 2024-09-27
---------------------------------------------------------------------- 
"""

import argparse
import requests
import re
import base64
import json
import time
import xmltodict
from datetime import datetime
import credentials

def get_headers():
    """Generate headers for the Freshservice API requests."""
    api_credentials = f"{credentials.fs_api_key}:{credentials.fs_password}"
    encoded_credentials = base64.b64encode(api_credentials.encode()).decode()
    return {
        'Content-Type': 'application/json',
        'Authorization': f'Basic {encoded_credentials}'
    }

def fetch_ticket_data(ticket_id, headers):
    """Fetch ticket data from Freshservice."""
    ticket_api = credentials.fs_domain
    url = f'https://{ticket_api}/api/v2/tickets/{ticket_id}'
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

def extract_employee_id(description_text):
    """Extract employee ID from the ticket description."""
    match = re.search(r"Employee ID:\s*(\d+)", description_text)
    if match:
        return match.group(1)
    else:
        raise ValueError("Employee ID not found in description text.")

def convert_date_format(date_str):
    """Convert date from MM/DD/YYYY to YYYY-MM-DD format."""
    date_str = date_str.strip()  # Remove any leading or trailing spaces
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        print(f"Error converting date: {date_str}")
        raise

def login_hrms():
    """Log in to HRMS and return the authentication token."""
    login_url = credentials.onePoint_login_domain
    headers = {
        "Api-Key": credentials.apiKey,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    login_data = {
        "credentials": {
            "username": credentials.username,
            "password": credentials.password,
            "company": credentials.company
        }
    }
    response = requests.post(login_url, headers=headers, data=json.dumps(login_data))
    response.raise_for_status()
    response_data = response.json()
    return response_data["token"]

def fetch_employee_data(employee_id, token):
    """Fetch employee data from HRMS."""
    report_url = credentials.onePoint_term_report
    headers = {
        "Accept": "application/xml",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    report_data = {
        "filters": [
            {
                "field_name": "EmployeeId",
                "operator": "=",
                "values": [f"{employee_id}"]
            }
        ]
    }
    response = requests.post(report_url, headers=headers, data=json.dumps(report_data))
    response.raise_for_status()
    response_dict = xmltodict.parse(response.content)
    if 'result' not in response_dict:
        raise ValueError("No result found in the response")
    return response_dict

def process_employee_data(response_dict):
    """Process employee data from HRMS response."""
    labels = [
        col["label"].strip().replace('\n', ' ').replace('  ', ' ')
        for col in response_dict["result"]["header"]["col"]
    ]
    rows = response_dict["result"]["body"]["row"]

    # Ensure rows is always a list
    if not isinstance(rows, list):
        rows = [rows]

    employee_data_list = []

    for row in rows:
        values = row["col"]
        aligned_data = dict(zip(labels, values))
        employee_data_list.append(aligned_data)

    # Assuming you want to process the first employee
    employee_data = employee_data_list[0]

    # Date conversion
    for date_field in ["Effective Date", "Date Hired", "Date Started", "IT Deactivation Date"]:
        if date_field in employee_data and employee_data[date_field]:
            print(f"Converting date: {employee_data[date_field]}")  # Debug statement
            employee_data[date_field] = convert_date_format(employee_data[date_field])
        else:
            print(f"Skipping date conversion for {date_field} (value is empty or not found).")

    return employee_data

def fetch_requester_info(email):
    """Fetch requester or agent ID and manager ID from Freshservice."""
    domain = credentials.fs_domain
    headers = get_headers()
    email = email.lower()
    
    # Define a helper function to perform the API query
    def search_api(endpoint, field_name):
        query = f'"{field_name}:\'{email}\'"'
        encoded_query = requests.utils.quote(query)
        url = f"https://{domain}/api/v2/{endpoint}?query={encoded_query}"
        print(f"Request URL: {url}")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    
    # First, search in the Requesters API using 'primary_email'
    try:
        response_json = search_api('requesters', 'primary_email')
        if response_json.get("requesters"):
            requester = response_json["requesters"][0]
            requester_id = requester["id"]
            manager_id = requester.get("reporting_manager_id")
            user_type = 'requester'
            return requester_id, manager_id, user_type
    except requests.HTTPError as e:
        print(f"Error searching requesters: {e}")
        print("Response Content:")
        print(e.response.content.decode())
    
    # If not found, search in the Agents API using 'email'
    try:
        response_json = search_api('agents', 'email')
        if response_json.get("agents"):
            agent = response_json["agents"][0]
            requester_id = agent["id"]
            manager_id = agent.get("reporting_manager_id")
            user_type = 'agent'
            return requester_id, manager_id, user_type
    except requests.HTTPError as e:
        print(f"Error searching agents: {e}")
        print("Response Content:")
        print(e.response.content.decode())
    
    # If not found in both, raise an error
    print("No user found with the given email.")
    raise ValueError("User not found in both requesters and agents")


def fetch_manager_email(manager_id):
    """Fetch manager email from Freshservice."""
    domain = credentials.fs_domain
    headers = get_headers()
    
    # Try fetching the manager as a requester first
    url = f"https://{domain}/api/v2/requesters/{manager_id}"
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        response_json = response.json()
        manager_email = response_json["requester"]["primary_email"]
        print(f"Manager found as requester. Email: {manager_email}")
        return manager_email
    elif response.status_code == 404:
        # If not found as a requester, try as an agent
        url = f"https://{domain}/api/v2/agents/{manager_id}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            response_json = response.json()
            manager_email = response_json["agent"]["email"]
            print(f"Manager found as agent. Email: {manager_email}")
            return manager_email
        else:
            print("Manager not found as agent.")
            response.raise_for_status()
    else:
        print("Failed to fetch manager.")
        response.raise_for_status()


def create_service_request(requester_id, it_deactivation_date, manager_email):
    """Create a service request in Freshservice."""
    domain = credentials.fs_domain
    ticket_url = f"https://{domain}/api/v2/service_catalog/items/95/place_request"

    # Default manager_email to 'noreply@hrms.com' if None
    if not manager_email:
        manager_email = 'noreply@hrms.com'

    service_request_data = {
        "quantity": 1,
        "email": manager_email,
        "custom_fields": {
            "date_of_change": f"{it_deactivation_date}",
            "untitled": f"{requester_id}",
            "employee_change_type": "Termination"
        }
    }
    headers = get_headers()

    # Log request details for debugging
    print("Request URL:", ticket_url)
    print("Request Data:", json.dumps(service_request_data, indent=4))

    response = requests.post(
        ticket_url,
        headers=headers,
        data=json.dumps(service_request_data),
        auth=(credentials.fs_api_key, credentials.fs_password)
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        print(f"Failed to create service request: {e}")
        print("Response Content:")
        print(response.content.decode())
        raise
    response_json = response.json()
    return response_json["service_request"]["id"]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch and process ticket data.")
    parser.add_argument("ticket_id", type=int, help="The ID of the ticket to fetch")
    args = parser.parse_args()

    headers = get_headers()
    ticket_data = fetch_ticket_data(args.ticket_id, headers)
    description_text = ticket_data['ticket']['description_text']

    try:
        employee_id = extract_employee_id(description_text)
        print(f"Employee ID: {employee_id}")
    except ValueError as e:
        print(e)
        return

    try:
        token = login_HRMS()
        time.sleep(5)  # Wait for 5-10 seconds as needed
        response_dict = fetch_employee_data(employee_id, token)
        employee_data = process_employee_data(response_dict)
        print(json.dumps(employee_data, indent=4))

        op_primary_email = employee_data["Primary Email"]
        it_deactivation_date = employee_data.get("IT Deactivation Date")
        employee_name = employee_data.get("Employee Name")  # Or construct from first and last name

        # Fetch requester or agent info
        try:
            requester_id, manager_id, user_type = fetch_requester_info(op_primary_email)
            print(f"User Type: {user_type.capitalize()}, ID: {requester_id}")

            if manager_id:
                try:
                    manager_email = fetch_manager_email(manager_id)
                    print(f"Manager Email: {manager_email}")
                except requests.HTTPError as e:
                    print(f"Failed to fetch manager email: {e}")
                    print("Response Content:")
                    print(e.response.content.decode())
                    manager_email = None
            else:
                print("Manager ID not found for the user")
                manager_email = None
        except ValueError as e:
            print(e)
            manager_email = None  # Default to None if user not found
            requester_id = None

        # Ensure requester_id is set
        if not requester_id:
            print("Requester ID not found. Cannot proceed without requester.")
            return  # Or decide on a fallback action

        # Create the service request
        try:
            ticket_id = create_service_request(requester_id, it_deactivation_date, manager_email)
            print(f"Service Request Ticket ID: {ticket_id}")
        except requests.HTTPError as e:
            print(f"Failed to create service request: {e}")
            print("Response Content:")
            print(e.response.content.decode())
        except Exception as e:
            print(f"An error occurred: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")



if __name__ == "__main__":
    main()
