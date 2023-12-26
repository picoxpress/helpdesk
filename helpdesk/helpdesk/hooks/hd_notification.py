import frappe

def handle_todo_entry(doc, method):
    if (doc.reference_type == "HD Ticket" and doc.status == "Open"):
        notification = frappe.new_doc("HD Notification")
        notification.user_from=doc.assigned_by if doc.assigned_by != "Guest" else "Administrator"
        notification.user_to=doc.allocated_to
        notification.notification_type="Assignment"
        notification.reference_ticket=doc.reference_name
        notification.save(ignore_permissions=True)
        frappe.db.commit()

