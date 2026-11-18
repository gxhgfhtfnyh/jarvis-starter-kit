"""
Minimal iCloud CalDAV client - add/edit/delete events and reminders on
the owner's Apple Calendar/Reminders (synced natively via iCloud to his phone).

Built plain on `requests` + `icalendar` instead of the `caldav` PyPI package,
which pulls in an experimental HTTP/2+3/OCSP stack (niquests/urllib3-future)
that threw a hard TypeError on every single request in testing (2026-08-06) -
not worth the fragility for a protocol this small.

Auth: app-specific Apple ID password, stored in Windows Credential Manager via
`keyring` (service "icloud_caldav", username = the owner's Apple ID email).
Never pass the password as a literal string anywhere in code or logs.

CalDAV flow (standard, same one every iCloud calendar client uses):
    1. PROPFIND /.well-known/caldav -> 301 redirect to the real partition host
    2. PROPFIND the principal URL for <calendar-home-set>
    3. PROPFIND the calendar-home URL (Depth:1) to list calendar collections
    4. PUT an .ics under a calendar collection to create an event/reminder
    5. REPORT (calendar-query) to list events in a date range
    6. DELETE a resource URL to remove an event/reminder
"""
import sys
import uuid
import datetime as dt
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from jarvis_keyring import get_password as keyring_get_password
import requests
from dateutil.rrule import rrulestr
from icalendar import Alarm, Calendar, Event, Todo

APPLE_ID = "kumthe owner@gmail.com"
DEFAULT_CALENDAR_NAME = "Home"  # chosen by the owner 2026-08-06
KEYRING_SERVICE = "icloud_caldav"
INITIAL_URL = "https://caldav.icloud.com/.well-known/caldav"

NS = {
    "d": "DAV:",
    "c": "urn:ietf:params:xml:ns:caldav",
    "cs": "http://calendarserver.org/ns/",
}


def _session():
    password = keyring_get_password(KEYRING_SERVICE, APPLE_ID)
    if not password:
        raise RuntimeError(
            f"No app-specific password in keyring for service={KEYRING_SERVICE!r} "
            f"username={APPLE_ID!r}. Generate one at appleid.apple.com and run:\n"
            f'  python -m keyring set {KEYRING_SERVICE} {APPLE_ID}'
        )
    s = requests.Session()
    s.auth = (APPLE_ID, password)
    s.headers["User-Agent"] = "JarvisCalendar/1.0"
    return s


def _local_naive(d):
    """A datetime as local naive time (aware values are converted to the local zone first); anything else is returned unchanged."""
    if isinstance(d, dt.datetime) and d.tzinfo is not None:
        return d.astimezone().replace(tzinfo=None)
    return d


def _expand_occurrences(component, window_start, window_end):
    """Returns [(start, end), ...] for a VEVENT's occurrences that fall in
    [window_start, window_end]. Handles plain one-off events and RRULE
    recurrence (all-day or timed), with EXDATE exclusions. Without this, a
    recurring event (e.g. an annual birthday) just shows its original
    creation date forever instead of the real upcoming occurrence."""
    dtstart_prop = component.get("dtstart")
    if dtstart_prop is None:
        return []
    dtstart = dtstart_prop.dt
    is_all_day = not isinstance(dtstart, dt.datetime)

    dtend_prop = component.get("dtend")
    if dtend_prop is not None:
        duration = dtend_prop.dt - dtstart
    else:
        duration = dt.timedelta(days=1) if is_all_day else dt.timedelta(0)

    rrule_prop = component.get("rrule")

    if rrule_prop is None:
        s = dtstart
        e = s + duration
        w_start = window_start.date() if is_all_day else _local_naive(window_start)
        w_end = window_end.date() if is_all_day else _local_naive(window_end)
        # 2026-10-30: a floating (naive) event against an aware window, or the reverse, raised TypeError and hid the whole calendar; compare both as local naive times
        if (s if is_all_day else _local_naive(s)) <= w_end and (e if is_all_day else _local_naive(e)) >= w_start:
            return [(s, e)]
        return []

    if is_all_day:
        rrule_dtstart = dt.datetime(dtstart.year, dtstart.month, dtstart.day)
        rrule_window_start = dt.datetime(window_start.year, window_start.month, window_start.day)
        rrule_window_end = dt.datetime(window_end.year, window_end.month, window_end.day, 23, 59, 59)
    else:
        rrule_dtstart = dtstart.replace(tzinfo=None) if dtstart.tzinfo else dtstart
        rrule_window_start = window_start.replace(tzinfo=None) if window_start.tzinfo else window_start
        rrule_window_end = window_end.replace(tzinfo=None) if window_end.tzinfo else window_end

    try:
        rule = rrulestr(f"RRULE:{rrule_prop.to_ical().decode()}", dtstart=rrule_dtstart)
    except Exception:
        return [(dtstart, dtstart + duration)]

    exdates = set()
    exdate_prop = component.get("exdate")
    if exdate_prop is not None:
        exdate_list = exdate_prop if isinstance(exdate_prop, list) else [exdate_prop]
        for ex in exdate_list:
            for d in ex.dts:
                val = d.dt
                exdates.add(val.date() if is_all_day and isinstance(val, dt.datetime) else val)

    occurrences = []
    for occ in rule.between(rrule_window_start, rrule_window_end, inc=True):
        s = occ.date() if is_all_day else occ
        if s in exdates:
            continue
        occurrences.append((s, s + duration))
    return occurrences


def _resolve(href_text, scheme, netloc):
    """iCloud sometimes returns hrefs as full absolute URLs and sometimes as
    bare paths - concatenating scheme://netloc onto an already-absolute href
    produces a mangled double URL, so detect and handle both."""
    if href_text.startswith("http://") or href_text.startswith("https://"):
        return href_text
    return f"{scheme}://{netloc}{href_text}"


def _propfind(session, url, body, depth="0"):
    resp = session.request(
        "PROPFIND",
        url,
        data=body,
        headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
        allow_redirects=False,
    )
    return resp


class ICloudCalDAV:
    """Discovers and holds the account's calendar-home + calendar list."""

    def __init__(self):
        self.session = _session()
        self.base_url = None
        self.principal_url = None
        self.calendar_home_url = None
        self.calendars = []  # list of dicts: {url, name, components}

    def discover(self):
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop>'
            "<d:current-user-principal/>"
            "</d:prop></d:propfind>"
        )
        resp = _propfind(self.session, INITIAL_URL, body)
        init_host = urlparse(INITIAL_URL)
        if resp.status_code in (301, 302, 307, 308):
            location = resp.headers["Location"]
            self.base_url = _resolve(location, init_host.scheme, init_host.netloc)
            resp = _propfind(self.session, self.base_url, body)
        else:
            self.base_url = INITIAL_URL

        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        href = root.find(".//d:current-user-principal/d:href", NS)
        if href is None:
            raise RuntimeError(f"Could not find current-user-principal. Response:\n{resp.text[:2000]}")

        host = urlparse(self.base_url)
        self.principal_url = _resolve(href.text, host.scheme, host.netloc)

        body2 = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><c:calendar-home-set/></d:prop></d:propfind>"
        )
        resp2 = _propfind(self.session, self.principal_url, body2)
        resp2.raise_for_status()
        root2 = ET.fromstring(resp2.content)
        home_href = root2.find(".//c:calendar-home-set/d:href", NS)
        if home_href is None:
            raise RuntimeError(f"Could not find calendar-home-set. Response:\n{resp2.text[:2000]}")
        principal_host = urlparse(self.principal_url)
        self.calendar_home_url = _resolve(home_href.text, principal_host.scheme, principal_host.netloc)

        body3 = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop>"
            "<d:displayname/><d:resourcetype/>"
            "<c:supported-calendar-component-set/>"
            "</d:prop></d:propfind>"
        )
        resp3 = _propfind(self.session, self.calendar_home_url, body3, depth="1")
        resp3.raise_for_status()
        root3 = ET.fromstring(resp3.content)
        home_host = urlparse(self.calendar_home_url)
        self.calendars = []
        for response in root3.findall("d:response", NS):
            href_el = response.find("d:href", NS)
            restype = response.find(".//d:resourcetype", NS)
            is_calendar = restype is not None and restype.find("c:calendar", NS) is not None
            if not is_calendar:
                continue
            name_el = response.find(".//d:displayname", NS)
            comps = [
                c.get("name")
                for c in response.findall(".//c:supported-calendar-component-set/c:comp", NS)
            ]
            self.calendars.append(
                {
                    "url": _resolve(href_el.text, home_host.scheme, home_host.netloc),
                    "name": name_el.text if name_el is not None and name_el.text else "(unnamed)",
                    "components": comps,
                }
            )
        return self.calendars

    def find_calendar(self, name_contains=None, component="VEVENT"):
        for cal in self.calendars:
            if component not in cal["components"]:
                continue
            if name_contains and name_contains.lower() not in cal["name"].lower():
                continue
            return cal
        return None

    def add_event(self, calendar_url, summary, start, end, description="", location="", alarm_minutes_before=None):
        """start/end: naive local datetime.datetime (assumes the owner's local timezone).
        alarm_minutes_before: int, or list of ints, for one or more VALARM reminders
        (e.g. 1440 for a day before) - without this, the event is silent, just a
        calendar entry with no phone notification."""
        cal = Calendar()
        cal.add("prodid", "-//Jarvis//icloud_calendar.py//EN")
        cal.add("version", "2.0")
        event = Event()
        uid = f"{uuid.uuid4()}@jarvis"
        event.add("uid", uid)
        event.add("summary", summary)
        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("dtstamp", dt.datetime.utcnow())
        if description:
            event.add("description", description)
        if location:
            event.add("location", location)
        if alarm_minutes_before is not None:
            offsets = alarm_minutes_before if isinstance(alarm_minutes_before, list) else [alarm_minutes_before]
            for minutes in offsets:
                alarm = Alarm()
                alarm.add("action", "DISPLAY")
                alarm.add("description", summary)
                alarm.add("trigger", dt.timedelta(minutes=-minutes))
                event.add_component(alarm)
        cal.add_component(event)

        resource_url = f"{calendar_url.rstrip('/')}/{uid}.ics"
        resp = self.session.put(
            resource_url,
            data=cal.to_ical(),
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        resp.raise_for_status()
        return resource_url, uid

    def add_reminder(self, list_url, title, due=None, notes=""):
        cal = Calendar()
        cal.add("prodid", "-//Jarvis//icloud_calendar.py//EN")
        cal.add("version", "2.0")
        todo = Todo()
        uid = f"{uuid.uuid4()}@jarvis"
        todo.add("uid", uid)
        todo.add("summary", title)
        todo.add("dtstamp", dt.datetime.utcnow())
        if due:
            todo.add("due", due)
        if notes:
            todo.add("description", notes)
        cal.add_component(todo)

        resource_url = f"{list_url.rstrip('/')}/{uid}.ics"
        resp = self.session.put(
            resource_url,
            data=cal.to_ical(),
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        resp.raise_for_status()
        return resource_url, uid

    def delete_resource(self, resource_url):
        resp = self.session.delete(resource_url)
        resp.raise_for_status()

    def list_events(self, calendar_url, start, end):
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start.strftime('%Y%m%dT%H%M%SZ')}" end="{end.strftime('%Y%m%dT%H%M%SZ')}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
        resp = self.session.request(
            "REPORT",
            calendar_url,
            data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        results = []
        for response in root.findall("d:response", NS):
            href_el = response.find("d:href", NS)
            data_el = response.find(".//c:calendar-data", NS)
            if data_el is None or not data_el.text:
                continue
            ical = Calendar.from_ical(data_el.text)
            for component in ical.walk("VEVENT"):
                for occ_start, occ_end in _expand_occurrences(component, start, end):
                    results.append(
                        {
                            "url": href_el.text,
                            "summary": str(component.get("summary")),
                            "start": occ_start,
                            "end": occ_end,
                        }
                    )
        return results

    def list_todos(self, list_url):
        """Lists all VTODOs (reminders) in a reminder list - no time-range
        filter, since most reminders have no due date at all."""
        body = """<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VTODO"/>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
        resp = self.session.request(
            "REPORT",
            list_url,
            data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        results = []
        for response in root.findall("d:response", NS):
            href_el = response.find("d:href", NS)
            data_el = response.find(".//c:calendar-data", NS)
            if data_el is None or not data_el.text:
                continue
            ical = Calendar.from_ical(data_el.text)
            for component in ical.walk("VTODO"):
                status = str(component.get("status") or "NEEDS-ACTION")
                results.append(
                    {
                        "url": href_el.text,
                        "summary": str(component.get("summary")),
                        "due": component.get("due").dt if component.get("due") else None,
                        "status": status,
                    }
                )
        return results


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    client = ICloudCalDAV()
    cals = client.discover()
    print(f"Connected. Found {len(cals)} calendars:")
    for c in cals:
        safe_name = c["name"].encode("ascii", "backslashreplace").decode("ascii")
        print(f"  - {safe_name!r} ({', '.join(c['components'])}) -> {c['url']}")
