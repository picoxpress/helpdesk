import frappe

from helpdesk.utils import extract_mentions


class HasMentions:
	def notify_mentions(self):
		"""
		Extract mentions from `mentions_field`, and notify.
		`mentions_field` must have `HTML` content.
		"""
		mentions_field = getattr(self, "mentions_field", None)
		if not mentions_field:
			return
		mentions = extract_mentions(self.get(mentions_field))
		for mention in mentions:
			values = frappe._dict(
				doctype="HD Notification",
				user_from=self.owner,
				user_to=mention.email,
				notification_type="Mention",
			)
			# Why mention oneself?
			if values.user_from == values.user_to:
				continue
			# Only comment (in tickets) has mentions as of now
			notification = frappe.new_doc("HD Notification")
			notification.user_from=self.owner
			notification.user_to=mention.email
			notification.notification_type="Mention"
			if self.doctype == "HD Ticket Comment":
				notification.reference_comment = self.name
				notification.reference_ticket = self.reference_ticket
			notification.save(ignore_permissions=True)
			frappe.db.commit()
