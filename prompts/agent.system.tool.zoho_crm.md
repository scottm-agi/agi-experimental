# Tool: Zoho CRM Toolkit

Interact with Zoho CRM for lead management.

## Usage

### Search Leads
Find leads based on Zoho CRM search criteria.
- **action**: "search_leads"
- **criteria**: (Optional) Zoho search criteria, e.g., `(Last_Name:equals:Smith)`.

### Create Lead
Add a new lead to Zoho CRM.
- **action**: "create_lead"
- **data**: JSON object containing lead fields (e.g., `{"Last_Name": "Doe", "Company": "Acme Inc"}`).

### Update Lead
Modify an existing lead.
- **action**: "update_lead"
- **lead_id**: The ID of the lead to update.
- **data**: JSON object containing fields to update.

### Delete Lead
Remove a lead from Zoho CRM.
- **action**: "delete_lead"
- **lead_id**: The ID of the lead to delete.

## Notes
- Search criteria must follow Zoho's `(Field:operator:Value)` format.
- Authorization is managed automatically via the the system's Universal OAuth profiles. If authorization is missing, the tool will report a failure with instructions.
