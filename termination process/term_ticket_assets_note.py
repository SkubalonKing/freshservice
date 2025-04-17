"""
----------------------------------------------------------------------
Script Name: ticket_assets_note.py

Summary:
    Automates asset reconciliation on a Freshservice ticket:
      1. Accepts a ticket ID from the command line.
      2. Pulls the ticket’s **requested items** and extracts the employee
         reference stored in the custom field `untitled`.
      3. Queries the Freshservice Assets API for every asset assigned to that
         employee and, for each, fetches its Asset‑Type metadata.
      4. Builds an HTML bullet list of the assets and adds it as a **public
         note** to the ticket so requesters see what must be collected.
      5. Updates the ticket’s `assets` relationship list so the same items
         appear in the ticket’s *Assets* tab.

Usage:
    python ticket_assets_note.py <ticket_id>
    Example:
    python ticket_assets_note.py 54157

Configuration:
    • `credentials.py` must define:
          fs_api_key, fs_password, fs_domain
    • The custom‑field key holding the employee reference is hard‑coded as
      `untitled`; change it if your Service Catalog item uses a different key.
    • Notes are posted publicly (`private: False`). Flip the flag if you need
      an internal‑only note.

Dependencies:
    • Python 3.9+
    • requests
    • Standard library: argparse, base64, json, datetime

Author: Sergio Gervacio
Date: 2024-09-26
---------------------------------------------------------------------- 
"""

import requests
import credentials
import base64
import json
from datetime import datetime, timezone
import argparse

def get_headers():
    """Generate headers for the API requests."""
    # Access the attributes of the credentials module
    api_credentials = f"{credentials.fs_api_key}:{credentials.fs_password}"
    encoded_credentials = base64.b64encode(api_credentials.encode()).decode()
    return {
        'Content-Type': 'application/json',
        'Authorization': f"Basic {encoded_credentials}"
    }

def fetch_requested_items(ticket_id, sandbox, headers):
    """Fetch requested items for a given ticket ID."""
    url = f'https://{sandbox}/api/v2/tickets/{ticket_id}/requested_items'
    response = requests.get(url, headers=headers)
    return response.json()

def fetch_assets(employee_name, sandbox, headers):
    """Fetch assets for a given user name."""
    url = f'https://{sandbox}/api/v2/assets?filter="user_id:{employee_name}"'
    response = requests.get(url, headers=headers)
    return response.json()

def get_asset_type(asset_type_id, sandbox, headers):
    """Get asset type data for a given asset type ID."""
    url = f'https://{sandbox}/api/v2/asset_types/{asset_type_id}'
    response = requests.get(url, headers=headers)
    return response.json()

def create_html_body(assets):
    """Convert a list of assets to a formatted HTML string."""
    assets_html = "".join([
        f"<li>Name: {asset['name']}, Type of Asset: {asset['asset_type']['name']}, Asset Tag: {asset['asset_tag']}</li>" for asset in assets
    ])
    return f"<p>Assets to be collected:</p><ul>{assets_html}</ul>"

def add_note_to_ticket(ticket_id, sandbox, headers, html_body):
    """Add a note to the ticket with the given HTML body."""
    url = f'https://{sandbox}/api/v2/tickets/{ticket_id}/notes'
    payload = {
        'body': html_body,
        'private': False
    }
    response = requests.post(url, headers=headers, data=json.dumps(payload))
    return response.status_code, response.text

def update_ticket_with_assets(ticket_id, sandbox, headers, assets):
    """Update the ticket with the fetched assets."""
    url = f'https://{sandbox}/api/v2/tickets/{ticket_id}'
    payload = {
        'assets': [
            {'display_id': asset['asset_tag']} for asset in assets
        ]
    }
    response = requests.put(url, headers=headers, data=json.dumps(payload))
    return response.status_code, response.text

def main(ticket_id):
    sandbox = credentials.fs_domain  # Access the sandbox URL from the credentials module
    headers = get_headers()

    # Fetch requested items
    requested_items = fetch_requested_items(ticket_id, sandbox, headers)

    if 'requested_items' in requested_items and requested_items['requested_items']:
        first_item = requested_items['requested_items'][0]
        employee_name = first_item['custom_fields'].get('untitled')

        if employee_name:
            print(f"User Name: {employee_name}")

            # Fetch assets for the user
            assets = fetch_assets(employee_name, sandbox, headers)

            assets_list = []
            for asset in assets.get('assets', []):
                asset_type_data = get_asset_type(asset.get('asset_type_id'), sandbox, headers)
                assets_list.append({
                    "name": asset.get('name'),
                    "asset_type": asset_type_data['asset_type'],
                    "asset_tag": asset.get('asset_tag')
                })
            # Create HTML body for note
            html_body = create_html_body(assets_list)

            status_code, response_text = add_note_to_ticket(ticket_id, sandbox, headers, html_body)
            print(status_code, response_text)

            # Update ticket with assets
            status_code, response_text = update_ticket_with_assets(ticket_id, sandbox, headers, assets_list)
            print(f"Ticket updated with assets: {status_code}, {response_text}")
        else:
            print("employee_name not found in custom_fields")
    else:
        print("requested_items not found or empty")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process ticket ID.')
    parser.add_argument('ticket_id', type=int, help='Ticket ID to process')
    args = parser.parse_args()
    main(args.ticket_id)
