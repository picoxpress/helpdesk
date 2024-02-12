import frappe
import requests
import json
from base64 import b64encode
from frappe.utils import get_site_url

# api/method/helpdesk.integrations.exotel_integration.handle_incoming_call
# api/method/helpdesk.integrations.exotel_integration.handle_end_call
# api/method/helpdesk.integrations.exotel_integration.handle_missed_call

@frappe.whitelist(allow_guest=True)
def handle_incoming_call(**kwargs):
    try:
        exotel_settings = get_exotel_settings()
        if not exotel_settings.enabled:
            return

        call_payload = kwargs
        status = call_payload.get("Status")
        if status == "free":
            return

        call_log = get_call_log(call_payload)
        if not call_log:
            return create_call_log(call_payload)
        else:
            return update_call_log(call_payload, call_log=call_log)
    except Exception as e:
        frappe.db.rollback()
        exotel_settings.log_error("Error in Exotel incoming call")
        frappe.db.commit()


@frappe.whitelist(allow_guest=True)
def handle_end_call(**kwargs):
    update_call_log(kwargs, "Completed")


@frappe.whitelist(allow_guest=True)
def handle_missed_call(**kwargs):
    status = ""
    call_type = kwargs.get("CallType")
    dial_call_status = kwargs.get("DialCallStatus")

    if call_type == "incomplete" and dial_call_status == "no-answer":
        status = "No Answer"
    elif call_type == "client-hangup" and dial_call_status == "canceled":
        status = "Canceled"
    elif call_type == "incomplete" and dial_call_status == "failed":
        status = "Failed"

    update_call_log(kwargs, status)

@frappe.whitelist(allow_guest=True)
def handle_outbound_summary(**data):
    update_outbound_call_log(data, "Completed")


def update_call_log(call_payload, status="Ringing", call_log=None):
    call_log = call_log or get_call_log(call_payload)
    # for a new sid, call_log and get_call_log will be empty so create a new log
    if not call_log:
        call_log = create_call_log(call_payload)
    if call_log:
        call_log.status = status
        call_log.to = call_payload.get("DialWhomNumber")
        call_log.duration = call_payload.get("DialCallDuration") or 0
        call_log.recording_url = call_payload.get("RecordingUrl")
        call_log.save(ignore_permissions=True)
        if status != "Ringing":
            ticket = get_ticket_for_call(call_payload)
            ticket.new_comment("Status of the Call - {}<br>Duration of the Call: {} Seconds<br>Recording URL: {}".format(status, call_log.duration, call_payload.get("RecordingUrl") or "Not Found"), True)
            ticket.save(ignore_permissions=True)
            frappe.db.commit()
        frappe.db.commit()
        return call_log


def update_outbound_call_log(call_payload, status="Completed", call_log=None):
    call_log = call_log or get_call_log(call_payload)
    # for a new sid, call_log and get_call_log will be empty so create a new log
    if not call_log:
        return
    if call_log:
        call_log.status = status
        call_log.duration = call_payload.get("ConversationDuration") or 0
        call_log.recording_url = call_payload.get("RecordingUrl")
        call_log.save(ignore_permissions=True)
        if call_payload.get("CustomField"):
            custom_payload = json.loads(call_payload.get("CustomField"))
            ticket = frappe.get_doc(custom_payload['type'], custom_payload['entityId'])
            if ticket:
                ticket.new_comment("Outbound Call - Completed<br>Duration of the Call: {} Seconds<br>Recording URL: {}".format(call_log.duration, call_payload.get("RecordingUrl") or "Not Found"), True)
                ticket.save(ignore_permissions=True)
                frappe.db.commit()
        frappe.db.commit()
        return call_log

def get_ticket_for_call(call_payload):
    subject = get_subject_for_call(call_payload)
    old_ticket = unresolved_ticket_with_subject_exists(subject)
    if old_ticket:
        return old_ticket
    return get_ticket_for_call_log(call_payload.get("CallSid"))

def get_ticket_for_call_log(call_log_id):
    QBTicket = frappe.qb.DocType("HD Ticket")
    ticket = frappe.qb.from_(QBTicket).select(
        QBTicket.name
    ).where(
        QBTicket.call_log == call_log_id
    ).limit(1).run(as_dict = True)
    if (len(ticket) > 0):
        return frappe.get_doc("HD Ticket", ticket[0].name)


def get_call_log(call_payload):
    call_log_id = call_payload.get("CallSid")
    if frappe.db.exists("Call Log", call_log_id):
        return frappe.get_doc("Call Log", call_log_id)


def create_call_log(call_payload):
    call_log = frappe.new_doc("Call Log")
    call_log.id = call_payload.get("CallSid")
    call_log.to = call_payload.get("DialWhomNumber")
    call_log.medium = call_payload.get("To")
    call_log.status = "Ringing"
    setattr(call_log, "from", call_payload.get("CallFrom"))
    call_log.save(ignore_permissions=True)
    create_helpdesk_ticket(call_log, get_subject_for_call(call_payload), call_payload)
    frappe.db.commit()
    return call_log

def create_outbound_call_log(call_payload):
    call_log = frappe.new_doc("Call Log")
    call_log.id = call_payload.get("Sid")
    call_log.to = call_payload.get("To")
    call_log.medium = call_payload.get("PhoneNumberSid")
    call_log.status = "Ringing"
    call_log.type = "Outgoing"
    setattr(call_log, "from", call_payload.get("From"))
    call_log.save(ignore_permissions=True)
    frappe.db.commit()
    return call_log

def get_subject_for_call(call_payload):
    return "Call From: {}".format(call_payload.get("CallFrom"))

def create_helpdesk_ticket(call_log, subject, call_payload, description=""):
    unresolved_ticket = unresolved_ticket_with_subject_exists(subject=subject)
    if unresolved_ticket:
        return unresolved_ticket
    ticket = frappe.new_doc("HD Ticket")
    ticket.description = description
    ticket.subject = subject
    ticket.call_log = call_log
    ticket.ticket_source = 'Telephony'
    agents = get_agents_with_number(call_log.to)
    ticket.save(ignore_permissions=True)
    if (len(agents) > 0):
        ticket.assign_agent(agents[0].user, True)
    frappe.db.commit()
    return ticket

def unresolved_ticket_with_subject_exists(subject):
    eligible_tickets = frappe.db.get_all(
        "HD Ticket",
        filters={
            "subject": ["=", subject],
            "status": ["not in", ["Resolved", "Closed"]]
        },
        order_by="creation desc",
        ignore_permissions=True
    )
    if (len(eligible_tickets) > 0):
        return frappe.get_doc("HD Ticket", eligible_tickets[0].name, ignore_permissions=True)
    return None

@frappe.whitelist()
def get_call_status(call_id):
    endpoint = get_exotel_endpoint("Calls/{call_id}.json".format(call_id=call_id))
    response = requests.get(endpoint)
    status = response.json().get("Call", {}).get("Status")
    return status

@frappe.whitelist(allow_guest=True)
def make_a_call_from_call_log(call_log_id, custom_field=None):
    call_log = frappe.get_doc("Call Log", call_log_id, ignore_permissions=True)
    if call_log:
        response = make_a_call(call_log.get('from'), call_log.to, "08068452182", custom_field)
        if 'Call' in response:
            return {
                'callSuccessful': True,
                'errorMessage': None
            }
        else:
            return {
                'callSuccessful': False,
                'errorMessage': response
            }


@frappe.whitelist(allow_guest=True)
def make_a_call(from_number, to_number, caller_id, custom_field=None):
    endpoint, token = get_exotel_endpoint_v2("Calls/connect.json")
    headers = {'Authorization': token}
    response = requests.post(
        endpoint, data={
            "From": from_number,
            "To": to_number,
            "CallerId": caller_id,
            "Record": "true",
            "StatusCallback": f"{get_site_url(frappe.local.site)}/api/method/helpdesk.helpdesk.integrations.exotel_integration.handle_outbound_summary",
            "StatusCallbackEvents[0]": "terminal",
            "CustomField": custom_field,
            "StatusCallbackContentType": "application/json"
        }, headers=headers
    )

    payload = response.json()
    if response.status_code == 200:
        call_log = create_outbound_call_log(payload['Call'])
        if custom_field:
            custom_payload = json.loads(custom_field)
            ticket = frappe.get_doc(custom_payload['type'], custom_payload['entityId'])
            if ticket:
                ticket.new_comment("Outbound Call - Started<br>From: {}<br>To:{}<br>Reference Id: {}".format(call_log.get('from'), call_log.to, call_log.id), True)
                ticket.save(ignore_permissions=True)
                frappe.db.commit()
    return payload

def basic_auth(username, password):
    token = b64encode(f"{username}:{password}".encode('utf-8')).decode("ascii")
    return f'Basic {token}'

@frappe.whitelist()
def make_a_call_from_call_id(from_number, to_number, caller_id):
    endpoint = get_exotel_endpoint("Calls/connect.json?details=true")
    response = requests.post(
        endpoint, data={"From": from_number, "To": to_number, "CallerId": caller_id}
    )

    return response.json()


def get_exotel_settings():
    return frappe.get_single("Exotel Settings")


def whitelist_numbers(numbers, caller_id):
    endpoint = get_exotel_endpoint("CustomerWhitelist")
    response = requests.post(
        endpoint,
        data={
            "VirtualNumber": caller_id,
            "Number": numbers,
        },
    )

    return response


def get_all_exophones():
    endpoint = get_exotel_endpoint("IncomingPhoneNumbers")
    response = requests.post(endpoint)
    return response


def get_exotel_endpoint(action):
    settings = get_exotel_settings()
    return "https://{api_key}:{api_token}@api.exotel.com/v1/Accounts/{sid}/{action}".format(
        api_key=settings.api_key, api_token=settings.api_token, sid=settings.account_sid, action=action
    )

def get_exotel_endpoint_v2(action):
    settings = get_exotel_settings()
    return [
        "https://api.exotel.com/v1/Accounts/{sid}/{action}".format(sid=settings.account_sid, action=action),
        basic_auth(settings.api_key, settings.get_password("api_token"))
    ]

def get_agents_with_number(number):
    number = strip_number(number)
    if not number:
        return []

    employee_doc_name_and_emails = frappe.cache().hget("employees_with_number", number)
    if employee_doc_name_and_emails:
        return employee_doc_name_and_emails

    employee_doc_name_and_emails = frappe.get_all(
        "HD Agent",
        filters={"cell_number": ["like", f"%{number}%"], "user": ["!=", ""]},
        fields=["name", "user"],
    )

    frappe.cache().hset("employees_with_number", number, employee_doc_name_and_emails)

    return employee_doc_name_and_emails

def strip_number(number):
    if not number:
        return
    # strip + and 0 from the start of the number for proper number comparisions
    # eg. +7888383332 should match with 7888383332
    # eg. 07888383332 should match with 7888383332
    number = number.lstrip("+")
    number = number.lstrip("0")
    return number