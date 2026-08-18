# Automation reservation-time UI v6

## Final editor structure

`시간대` is no longer offered as a standalone condition for new TriggerSets.
Use `예약 시간 > 요일 선택 반복` and then choose one of the two time modes:

- `지정 시각`: wakes/evaluates the TriggerSet once at the selected clock time on each checked weekday.
- `시간대`: passive permission window. It does not wake the TriggerSet by itself; a device-state/MQTT condition must cause evaluation.

For `시간대`, configure:

- repeat weekdays (Mon..Sun)
- start time
- optional end time
- `종료 시간 제한 없음` means start time through midnight on each selected weekday.

A window that crosses midnight (for example Monday 23:00~06:00) is anchored to the selected start weekday and remains valid until Tuesday 06:00.

## Example

Weekdays after 18:30 + door open + projector off -> projector on:

- condition operator: AND
- reservation time: Mon/Tue/Wed/Thu/Fri, time mode `시간대`, start 18:30, no end
- device state: door `contact == false`
- device state: projector `power == false`
- action: projector power on

18:30 itself does not execute the action. A later relevant state change causes the set to be evaluated against the time window.

## Migration

`0022_merge_time_window_into_schedule` converts standalone legacy `time_window` rows into the new weekly reservation-time window representation when the same owner does not already contain a reservation-time condition. Ambiguous legacy pairs are left intact and remain editable as `시간대 (기존)` rather than being guessed/rewritten.
