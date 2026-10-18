# Demo video

[Watch the 2-minute demo](https://www.youtube.com/watch?v=[video-id]) (unlisted).

## What it shows

1. **The problem (20 s).** A shared inbox with the same questions arriving all day: fees, documents, deadlines, appointments.
2. **The review queue (90 s).** `docker compose up`, then:
   - a pricing enquiry arrives, the draft quotes the fee list with a `[2]` citation, the reviewer edits one line and presses Approve and send;
   - "What the model saw": the same enquiry with the sender's name, phone and postcode replaced by placeholders;
   - an enquiry the documents do not cover becomes a holding reply and an escalation, with the model's notes for the reviewer;
   - a complaint is flagged "a person first" and never gets an auto-drafted answer.
3. **The numbers (30 s).** `make evals`: the report table, the figures guard count, the cost per enquiry.

## Screens

- `review-screen.png`: enquiry, draft and sources side by side, demo data.
- `inbox.png`: the queue with status, category, confidence and cost per enquiry.
