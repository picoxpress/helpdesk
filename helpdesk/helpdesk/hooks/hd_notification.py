import frappe

def handle_todo_entry(doc, method):
    if (doc.reference_type == "HD Ticket" and doc.status == "Open"):
        values = frappe._dict(
            doctype="HD Notification",
            user_from=doc.assigned_by,
            user_to=doc.allocated_to,
            notification_type="Assignment",
            reference_ticket=doc.reference_name
        )
        frappe.get_doc(values).insert()

