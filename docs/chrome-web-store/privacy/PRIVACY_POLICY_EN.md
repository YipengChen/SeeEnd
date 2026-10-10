# SeeEnd Browser Extension Privacy Policy

Policy date: October 10, 2026. Applies to SeeEnd AI web chat status extension version 0.2.10.

## Purpose

The extension observes supported AI website controls for user-initiated text chats, and sends generation, completion or interruption status to the SeeEnd desktop app on the same computer. The desktop app performs the user's configured reminders. Installing the extension does not install the desktop app.

## Local processing

On supported pages, the extension processes the current page URL, conversation route, reply-node identifiers, control structure and attributes, and submit, Enter, stop, navigation and visibility events. These are used to identify the site and current task and avoid reporting historical replies or interruptions as completion. Actual page URLs and website reply identifiers remain in page memory and are not included in status events. The extension does not access the browser-history database.

The extension does not read or store prompts, response text, editor contents, conversation titles, account details, passwords or authentication credentials. It does not record general typed characters or inspect AI website network responses.

Status events include a schema version, random event and task identifiers, site source, sequence, state, timestamp, time-to-live and optional end reason. Chrome Native Messaging delivers these events to the local com.aiflame.chatgpt bridge, which forwards them to the SeeEnd service on loopback address 127.0.0.1. No actual conversation URL or website reply identifier is included.

## Storage and diagnostics

Session storage holds active task records, removed on completion or cancellation and cleared when the browser session ends. Local storage holds the latest delivery result for each site and the latest overall result, including time, source, state, delivery result, connection errors and an optional cancellation reason. New results overwrite old results; there is no automatic age-based deletion.

Popup diagnostics show control types, CSS classes, test attributes, layout and rendering counts in the current session. They do not show chat or editor text and are not uploaded to developer-operated servers.

The desktop app stores received status events and activity records locally with event history limited to seven days or 10,000 entries. Active tasks remain until completion or lease expiry; users can also clear or reset the app. Removing the extension does not delete desktop records, device settings or reminder preferences.

## Use and sharing

The extension does not upload these data to developer-operated servers or include advertising or analytics SDKs. Data are not sold, used for advertising profiles, creditworthiness or lending. Data are used only for the stated reminders, connection checks and local diagnostics. SeeEnd's use of relevant user data complies with the Chrome Web Store User Data Policy, including Limited Use requirements.

AI websites have their own data policies. Google provides store installation, updates and review independently of this extension.

## User controls

Disable or remove the extension in Chrome to stop its processing. Removing it clears extension storage. Desktop source switches and pause controls affect reminders, not necessarily browser-page observation. Desktop records can be removed with Reset App, which also clears device pairings, reminder preferences and integration settings; review the confirmation carefully.

## Contact and changes

This policy will be updated if data practices change. Publisher: Yipeng Chen. Privacy contact: ypchenmail@gmail.com.
