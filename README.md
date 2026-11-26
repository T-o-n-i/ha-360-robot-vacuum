# 360 Robot Vacuum für Home Assistant

Custom Integration für Saugroboter von Qihoo 360, die mit der App **360Robot** gesteuert werden
(Cloud `q.smart.360.cn`). Geschrieben für den **360 S6**; andere Modelle derselben App sollten
ähnlich funktionieren.

## Was sie kann

- Anmeldung wie in der 360Robot-App (E-Mail oder Handynummer, Passwort). Die Sitzung wird automatisch erneuert.
- Alle Roboter des Kontos werden als eigene Geräte angelegt.
- Status kommt live über den Push-Kanal der 360-Cloud, zusätzlich wird jede Minute abgefragt.
- Pro Roboter:
  - `vacuum.*`: Start, Pause, Stopp (= Pause), zurück zur Station, Saugstärke (`quiet`, `auto`, `strong`), Orten
  - Sensoren: Akku, Status (Rohstatus als Attribute), Fehlercode, gereinigte Fläche, Reinigungsdauer, letzte Statusmeldung
  - Diagnose-Sensoren: Laufzeit von Filter, Haupt- und Seitenbürste und Sensoren in Stunden (stündlich abgefragt)
  - Schieberegler für die Lautstärke (0–10)
- `vacuum.send_command` für alles andere. `command` ist der infoType, `params` die Daten, z. B. Lautstärke:

  ```yaml
  action: vacuum.send_command
  target:
    entity_id: vacuum.erdgeschoss
  data:
    command: "21024"
    params: { cmd: setVolume, value: 5 }
  ```

## Installation

1. Ordner `custom_components/robot360` nach `/config/custom_components/robot360` kopieren
   (Samba, SSH oder File-Editor). Alternativ das Repo als benutzerdefiniertes HACS-Repository eintragen.
2. Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → „360 Robot Vacuum“*, E-Mail und Passwort eingeben.

Verlangt 360 ein Captcha, zeigt der Dialog das Bild an.

## Testlauf ohne Home Assistant

```bash
python3 -m venv .venv && .venv/bin/pip install aiohttp cryptography
.venv/bin/python tools/probe.py
```

Das Skript meldet sich an, listet die Roboter, fragt Status und Verbrauchsmaterial ab und schreibt das Ergebnis
ohne Passwort und Sitzungsschlüssel nach `tools/probe_output.json`. Mit `-v` gibt es ausführliche Logs.

## Bekannte Grenzen

- Die Feldnamen sind an einer S6 mit Firmware 2.10.10 geprüft. Bisher gesehen hat die Integration nur den Modus `fullcharge`. Die anderen Modi (Saugen, Rückfahrt, Pause) stammen aus juetthei/ha-360-vacuum. Unbekannte Modi stehen trotzdem im Status-Sensor.
- Die Laufzeiten sind die Betriebsstunden seit dem letzten Zurücksetzen. Wie viele Stunden ein Filter laut 360 hält, liefert die Cloud nicht mit.
- Nur Cloud. Ohne Internet oder bei Änderungen an der 360-API funktioniert nichts mehr.
- Keine Karte, keine Raum- oder Zonenreinigung.

## Herkunft

Login-Ablauf und Push-Kanal sind aus [TA2k/ioBroker.botslab360](https://github.com/TA2k/ioBroker.botslab360) (MIT)
nach Python übertragen, die Befehle stammen aus [juetthei/ha-360-vacuum](https://github.com/juetthei/ha-360-vacuum) (MIT),
das Frame-Format aus [cavefire/cn360-proxy](https://github.com/cavefire/cn360-proxy).
Kein offizielles Produkt von Qihoo 360.
