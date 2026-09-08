# Sample data — entirely fictional

Every vendor, invoice, address, account number and VAT identifier in this directory is
invented. There is no Stadtwerke Musterstadt, no Kabelnetz Musterland and no StreamCo
Europe B.V.

*Musterstadt* is the German equivalent of "Anytown", and *Voorbeeld* is Dutch for
"example". `DE999999999` is not an allocated VAT number.

This matters because the whole point of the samples is to show a vendor **billing
incorrectly**. Attaching that to a real company — even accidentally, even in a fixture —
would be an accusation, so the data is deliberately unmistakable as fiction.

## The three scenarios

| Folder | What it demonstrates |
|---|---|
| `kabelnetz/` | A flawless bill. The agent must say **nothing**. This is the important one. |
| `stadtwerke/` | Four real problems on one invoice: an off-contract rate, an arithmetic mismatch, a duplicated service fee, and an unagreed price rise. €26.90 at stake. |
| `streamco/` | A bill that is arithmetically perfect and has auto-renewed for nine months. Not an error — a decision. |

## Layout

```
<vendor>/
    ratecard.json     what the account holder believes they agreed to pay
    history.json      previously processed bills, used to detect drift
    inbox/            raw documents not yet looked at
```

Adding a fourth vendor needs no code changes — drop in a folder of the same shape.
