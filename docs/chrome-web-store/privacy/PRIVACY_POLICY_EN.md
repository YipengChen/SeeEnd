# SeeEnd Browser Extension Privacy Policy

Policy date: October 16, 2026. Applies to the local 0.2.12 draft of the SeeEnd AI web chat status extension. Local changes do not imply store publication.

## Purpose

The extension observes supported AI website controls for user-initiated text chats, and sends generation, completion or interruption status to the SeeEnd desktop app on the same computer. The desktop app performs the user's configured reminders and forwards user-initiated return requests to the original browser profile. Installing the extension does not install the desktop app.

## Local processing

On supported pages, the extension processes the current page URL, conversation route, reply-node identifiers, control structure and attributes, and submit, Enter, stop, navigation and visibility events. These are used to identify the site and current task and avoid reporting historical replies or interruptions as completion. Conversation URLs, with unrelated query parameters removed, and original tab locations for tracked tasks are also saved in extension-local storage so users can return to a task. Website reply identifiers remain in page memory. Neither URLs nor website reply identifiers are included in status events. The extension does not access the browser-history database.

The extension does not read or store prompts, response text, editor contents, conversation titles, account details, passwords or authentication credentials. It does not record general typed characters or inspect AI website network responses.

Status events include a schema version, random event and task identifiers, site source, sequence, state, timestamp, time-to-live, optional end reason and optional opaque browser-profile and return-location identifiers. Chrome Native Messaging delivers these events to the local com.aiflame.chatgpt bridge, which forwards them to the SeeEnd service on loopback address 127.0.0.1. No actual conversation URL or website reply identifier is included.

## Storage and diagnostics

Extension-local storage also keeps tracked-task conversation URLs, original tab and document identities, timestamps and a random browser-profile identifier for user-initiated returns. At most 1,000 destinations remain usable for seven days; expired and excess records are removed when a task updates or a return is requested. Old tab IDs are never reused after browser restart. A stable conversation URL can be reopened in its originating profile; destinations without a recoverable URL report that they are unavailable. This adds no tabs permission or site access. Incognito tabs do not register return destinations.

Session storage holds active task records, removed on completion or cancellation and cleared when the browser session ends. Local storage holds the latest delivery result for each site and the latest overall result, including time, source, state, delivery result, connection errors and an optional cancellation reason. New results overwrite old results; there is no automatic age-based deletion.

Popup diagnostics show control types, CSS classes, test attributes, layout and rendering counts in the current session. They do not show chat or editor text and are not uploaded to developer-operated servers.

The desktop app stores received status events and activity records locally with event history limited to seven days or 10,000 entries. Active tasks remain until completion or lease expiry; users can also clear or reset the app. Removing the extension does not delete desktop records, device settings or reminder preferences.

## Use and sharing

The extension does not upload these data to developer-operated servers or include advertising or analytics SDKs. Data are not sold, used for advertising profiles, creditworthiness or lending. Data are used only for the stated reminders, user-initiated task returns, connection checks and local diagnostics. SeeEnd's use of relevant user data complies with the Chrome Web Store User Data Policy, including Limited Use requirements.

AI websites have their own data policies. Google provides store installation, updates and review independently of this extension.

## User controls

Disable or remove the extension in Chrome to stop its processing. Removing it clears extension storage. Desktop source switches affect reminders, not necessarily browser-page observation. Desktop records can be removed with Reset App, which also clears device pairings, reminder preferences and local integration records. Installed external hooks, plugins and browser extensions remain and must be removed separately; review the confirmation carefully.

## Contact and changes

This policy will be updated if data practices change. Publisher: Yipeng Chen. Privacy contact: ypchenmail@gmail.com.
