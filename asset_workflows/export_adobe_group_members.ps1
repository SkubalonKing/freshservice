<#
----------------------------------------------------------------------
Script Name: export_adobe_group_members.ps1

Summary:
    Automates license‑auditing and ticket updates for the *Adobe Cloud
    Account Provisioning* security group:
      1. Accepts a Freshservice **ticket ID** via the `‑ticketId` parameter.
      2. Decrypts a locally stored password (`password.txt`) and builds a
         credential object using `$env:ORCH_USERNAME`.
      3. Runs a remote script block on the domain controller defined in
         `$env:REMOTE_DOMAIN_CONTROLLER` to collect each group member’s
         title, location, office, display name, and SamAccountName.
      4. Exports that data to **AdobeGroupMembers.csv** and logs every step
         to `C:\PATH\operations_report_log.txt`.
      5. Uploads the CSV as an attachment to the Freshservice ticket and
         sets the ticket priority to 1 via a multipart `PUT` request.
      6. Deletes the CSV once the API call succeeds.

Usage:
    .\export_adobe_group_members.ps1 ‑ticketId 54157

Configuration:
    • Environment variables required:
          ORCH_USERNAME              # service account for remote execution
          REMOTE_DOMAIN_CONTROLLER   # target DC for Invoke‑Command
          FS_API_KEY                 # Freshservice API key
          FS_LIVE_DOMAIN             # Freshservice subdomain (e.g. kingsview.freshservice.com)
    • Encrypt the service‑account password *as the same user*:
          Read-Host -AsSecureString | ConvertFrom-SecureString |
          Set-Content C:\PATH\password.txt
    • Log file and CSV paths are hard‑coded under `C:\PATH\`.

Dependencies:
    • Windows PowerShell 5.1+ (or PowerShell 7 with ActiveDirectory module)
    • ActiveDirectory module on the remote server
    • Network access from script host to the domain controller and Freshservice API

Author: Sergio Gervacio
Date: 2025‑04‑17
----------------------------------------------------------------------
#>


param (
    [int]$ticketId  # Accepts the ticket ID as an argument
)

# Define the log file path
$logFilePath = "C:\PATH\operations_report_log.txt"

# Function to write log messages
function Write-Log {
    param (
        [string]$message
    )
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "$timestamp - $message"
    Add-Content -Path $logFilePath -Value $logMessage
}

# Log the start of the script
Write-Log "Script Started"


# Define username for remote access
$username = $env:ORCH_USERNAME
Write-Log "Using $username to run Invoke command"


# Try reading the encrypted password and catch any errors
try {
    # Read the encrypted password from the file and convert it to a secure string
    $encryptedPassword = Get-Content "C:\PATH\password.txt" | ConvertTo-SecureString
    Write-Log "Password decrypted successfully."
} catch {
    Write-Log "Error: Unable to decrypt the password. Ensure the password was encrypted using the same user."
    exit 1
}

# Create a PSCredential object using the username and the secure string password
try {
    $credentials = New-Object System.Management.Automation.PSCredential ($username, $encryptedPassword)
    Write-Log "Credential object created successfully."
} catch {
    Write-Log "Error: Failed to create credential object."
    exit 1
}

# Define the remote server to connect to
$remoteServer = $env:REMOTE_DOMAIN_CONTROLLER

# Define the group name
$groupName = "Adobe Cloud Account Provisioning"

# Define the script block to run on the remote server
$scriptBlock = {
    param($groupName)

    # Get the group members
    $groupMembers = Get-ADGroupMember -Identity $groupName -Recursive

    # Initialize an array to hold the results
    $results = @()

    # Loop through each member
    foreach ($member in $groupMembers) {
        # Get the user's properties
        $user = Get-ADUser -Identity $member.SamAccountName -Properties department, physicalDeliveryOfficeName, title
        
        # Create a custom object to hold the user's information
        $userInfo = New-Object PSObject -Property @{
            Title = $user.title
            Location = $user.physicalDeliveryOfficeName
            Office   = $user.department
            Name     = $user.Name
            SamAccountName = $user.SamAccountName
        }
        
        # Add the user's information to the results array
        $results += $userInfo
    }

    # Return the results
    return $results
}

# Invoke the script block on the remote server
$groupMembers = Invoke-Command -ComputerName $remoteServer -ScriptBlock $scriptBlock -ArgumentList $groupName -Credential $credentials

# Check if groupMembers were retrieved successfully
if (-not $groupMembers) {
    Write-Log "Error: Failed to retrieve group members from the remote server."
    Write-Host "Error: Failed to retrieve group members from the remote server."
    exit 1
} else {
    Write-Log "Successfully retrieved group members."
}

# Export the group members to a CSV file
$csvPath = "C:\PATH\AdobeGroupMembers.csv"
try {
    $groupMembers | Export-Csv -Path $csvPath -NoTypeInformation
    Write-Log "CSV file created at $csvPath."
} catch {
    Write-Log "Error: Failed to create CSV file."
    exit 1
}

# Ensure the CSV file exists before proceeding
if (-not (Test-Path $csvPath)) {
    Write-Log "Error: CSV file not found after export."
    exit 1
}

# Variables for the Freshservice API
$apiKey = $env:FS_API_KEY # Replace with your actual Freshservice API key
$domain = $env:FS_LIVE_DOMAIN  # Replace with your Freshservice domain
Write-Log "Sending API request to Freshservice for ticket ID: $ticketId."

# URL for the Freshservice API
$url = "https://$domain/api/v2/tickets/$ticketId"

# Headers for the request
$headers = @{
    "Authorization" = "Basic " + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("$($apiKey):X"))
}

# Build the multipart form-data content
$boundary = [System.Guid]::NewGuid().ToString()
$headers.Add("Content-Type", "multipart/form-data; boundary=$boundary")

# Create the multipart body manually
$body = "--$boundary`r`n"
$body += "Content-Disposition: form-data; name=`"priority`"`r`n`r`n"
$body += "1`r`n"
$body += "--$boundary`r`n"
$body += "Content-Disposition: form-data; name=`"attachments[]`"; filename=`"$([System.IO.Path]::GetFileName($csvPath))`"`r`n"
$body += "Content-Type: application/octet-stream`r`n`r`n"
$body += [System.IO.File]::ReadAllText($csvPath)
$body += "`r`n--$boundary--`r`n"

# Convert the body string to byte array
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)

# Send the request using Invoke-WebRequest
try {
    $response = Invoke-WebRequest -Uri $url -Method Put -Headers $headers -Body $bodyBytes
    Write-Log "API request sent. Response code: $($response.StatusCode)"
    Write-Host "API request sent. Response code: $($response.StatusCode)"
} catch {
    Write-Log "Error: Failed to send API request: $($response.StatusCode)"
    Write-Host "Error: Failed to send API request: $($response.StatusCode)"
}

# Output the response
$responseContent = $response.Content
Write-Log "API response content: $responseContent"



# Delete the CSV file after use if it exists
if (Test-Path $csvPath) {
    Remove-Item -Path $csvPath -Force
    Write-Log "GroupMembers.csv has been deleted."
} else {
    Write-Log "Error: CSV file not found for deletion."
}

Write-Log "Script Completed"

