# REST API

[English](../api.md) · [Alle documentatie](README.md)

Mailfilter heeft een REST API voor hostingpanels en scripts: domeinen
toevoegen, een webserver laten relayen, SMTP-accounts maken, mail uit
quarantaine vrijgeven, zoeken in de mail trace en meer.

## API-sleutels

Maak een sleutel onder **Systeem › API › API-sleutels**. Kies per onderdeel wat
de sleutel mag (geen, lezen, schrijven), en eventueel vanaf welke IP-adressen
hij gebruikt mag worden en tot wanneer. De sleutel (`mfk_…`) zie je één keer;
Mailfilter bewaart alleen de SHA-256.

- Een sleutel werkt als zijn **eigenaar**: een admin-sleutel op alle domeinen,
  een klant-sleutel alleen op de domeinen van die klant. Admins kunnen een
  sleutel voor een klant maken.
- Alles wat met een sleutel gebeurt staat in de audit log, met "via
  API-sleutel <naam>".
- Sleutels worden tussen clusterservers gesynchroniseerd.

## De API gebruiken

```
curl https://mailfilter.example.nl/api/v1/gateway/relay-networks \
  -H "Authorization: Bearer mfk_…" \
  -H "Content-Type: application/json" \
  -d '{"cidr": "203.0.113.10", "domain": "klant.nl", "description": "Webserver"}'
```

- Basis-URL `/api/v1`; alleen sleutels, geen sessiecookie.
- Lezen staat GET toe, schrijven alle methodes.
- Maximaal 600 verzoeken per sleutel per minuut (429 met `Retry-After`).
- `GET /api/v1/me` laat zien wat de sleutel mag.
- De OpenAPI-beschrijving staat op `/api/v1/openapi.json`; het tabblad
  Documentatie in de app toont alle endpoints met voorbeelden.

Nooit met een sleutel: API-sleutels zelf, updates, backups, cluster,
throttling en andere systeeminstellingen (`403 not_available_in_api`).
