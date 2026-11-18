# -*- coding: utf-8 -*-
"""Send a real email through the desktop Outlook app (COM automation via pywin32), using whatever
account/profile is already signed in there -- no credentials touched, nothing left in a draft folder
unless --draft is given. This is a real send to a real person: only use it on content the owner has
already approved (rule 13).

    python send_outlook_mail.py --to "a@b.com" --subject "..." --body "..." [--attach "C:\\path\\file.docx"]
    python send_outlook_mail.py --to "a@b.com" --cc "c@d.com,e@f.com" --subject "..." --stdin < body.txt
    python send_outlook_mail.py ... --draft     # composes and saves to Drafts instead of sending

Exit codes: 0 sent/saved, 1 Outlook COM unavailable, 2 bad arguments.
"""
import sys


def _arg(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def send(to, subject, body, cc=None, attach=None, draft=False):
    """Pure-ish: the one side effect is the win32com call. Returns a short result string."""
    import win32com.client

    outlook = win32com.client.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)  # olMailItem
    mail.To = to
    if cc:
        mail.CC = cc
    mail.Subject = subject
    mail.Body = body
    for path in (attach or []):
        mail.Attachments.Add(path)
    if draft:
        mail.Save()
        return "saved to Drafts"
    mail.Send()
    return "sent"


def main():
    args = sys.argv[1:]
    to = _arg(args, "--to")
    subject = _arg(args, "--subject")
    cc = _arg(args, "--cc")
    attach = [a for a in (_arg(args, "--attach"),) if a]
    draft = "--draft" in args
    if "--stdin" in args:
        body = sys.stdin.read()
    else:
        body = _arg(args, "--body", "")
    if not to or not subject:
        print("need --to and --subject", file=sys.stderr)
        sys.exit(2)
    try:
        result = send(to, subject, body, cc=cc, attach=attach, draft=draft)
    except Exception as e:
        print("Outlook COM error: %s" % e, file=sys.stderr)
        sys.exit(1)
    print(result)


if __name__ == "__main__":
    main()
