# UK cold-outreach rules and Companies House matching

**Status: research notes, not legal advice.** Compiled 2026-09-20 from web sources (all accessed that day; see Sources). ICO guidance pages carry a banner saying they are "under review" after the Data (Use and Access) Act, so re-check before relying on any single line [S2].

## Plain-English summary

You may cold-email or text a *company* (Ltd, PLC, LLP, Scottish partnership, public body) without consent, but **not** a *sole trader or ordinary partnership* unless they already consented or were a customer; there is no "first cold email" exception. Every marketing email must say who you are and carry a working opt-out, and opt-outs (including from companies) must be honoured. Cold *calls* to any business are allowed unless the number is on the TPS or CTPS registers, so screen first. *Letters* are almost unregulated. Since 5 February 2026 the maximum PECR fine is £17.5m / 4% of turnover, so the sole-trader test matters. Companies House's free API tells you whether a name at a postcode is a registered company, which is the practical way to sort leads.

## 1. PECR and unsolicited B2B marketing email

**Who is who.** The ICO's B2B guidance lists *corporate subscribers* as companies, corporations sole, LLPs, Scottish partnerships, some government bodies and "any other body corporate with distinct legal identity". *Individual subscribers* are sole traders, non-limited-liability partnerships in England, Wales and Northern Ireland, and unincorporated bodies of individuals [S1]. A PLC is a company, so corporate.

**Consent.** Regulation 22 applies only to "individual subscribers": no unsolicited direct-marketing electronic mail without prior consent, except under the soft opt-in [S5]. ICO: "You can send unsolicited electronic mail marketing to corporate subscribers without consent or a soft opt-in" [S3]; but the individual rules "also apply to sole traders and people who work for themselves" [S4].

**Soft opt-in does not cover a first cold email.** Reg 22(3) requires details obtained "in the course of the sale or negotiations for the sale" to that recipient, similar products only, and an opt-out at collection and in every message [S5]; it "does not apply to prospective customers or new contacts" [S2]. A sole trader found on Google Places cannot be cold-emailed.

**Mandatory content (corporate recipients included).** Reg 23 bans email where the sender's identity "has been disguised or concealed" or no "valid address" for cease requests is given [S6]; "you must say who you are and tell them how to opt-out ... on every such message" [S4]; and "you should comply with a corporate subscriber's opt-out request" [S1].

**Named person at a company (firstname@company.co.uk).** ICO: "the email address or telephone number of an employee at a corporate body would constitute a corporate subscriber", so PECR consent is not needed; but UK GDPR applies because the address identifies a person: lawful basis, absolute right to object, privacy information within one month of collection [S1]. A public address is not consent ("you cannot assume ... they are agreeing to it being used for direct marketing") [S1]. And "You can't rely on an email address to determine whether a person is a sole trader or a limited company" [S4].

**Legitimate interests.** The ICO lists "Legitimate interests or consent" for electronic mail to corporate subscribers and for post [S7]; LI is "unlikely to be appropriate" where PECR demands consent [S4]. The DUAA added direct marketing to UK GDPR's statutory examples of legitimate interests, but the three-part test/LIA still applies [S8, S10].

**DUAA 2025 (in force 5 February 2026)** [S9, S10]: PECR fines raised from £500,000 to "up to £17.5 million or 4% of global turnover" [S9]; charity-only soft opt-in added as reg 22(3A) [S5, S8]; analytics/appearance cookies no longer need consent [S8]. **No change** to the corporate/individual split or B2B email rules [S10]; ICO mail guidance is "under review" [S2].

## 2. Companies House public data API

- **Access.** Register a Companies House developer account, create a "live" application, "Create new key" [S17]. HTTP Basic auth, key as username, blank password: `curl -u my_api_key: https://api.company-information.service.gov.uk/company/00000006` [S16]. No fee is listed in the guides.
- **Rate limit.** 600 requests per five minutes per key across all endpoints; excess returns 429 until the window resets; higher limits on request [S11].
- **Search.** `GET /search/companies?q=&items_per_page=&start_index=&restrictions=active-companies` [S12]. Items: `company_number`, `company_status`, `company_type`, `title`, `date_of_creation`, `address{locality, postal_code, ...}`, `address_snippet`, `matches.title[]` [S14].
- **Advanced search.** `GET /advanced-search/companies` with `company_name_includes`, `location`, `company_status`, `company_type`, `sic_codes`, `size` (1–5000); returns the same plus `company_subtype`, `registered_office_address{postal_code, ...}`, `sic_codes` [S13].
- **Profile.** `GET /company/{companyNumber}`: `company_status` (`active`, `dissolved`, `liquidation`...), `type` (`ltd`, `plc`, `llp`, `private-unlimited`, `limited-partnership`...), `registered_office_address`, `sic_codes` [S15]. Zero-pad numbers to eight characters; dissolved companies still return HTTP 200, so check `company_status` [S19].
- **Bulk alternative.** The Free Company Data Product: monthly CSV snapshot of live companies (469 MB zip or seven parts) with name, number, registered address and postcode, category, status, SIC codes [S18]. Good for offline matching with no rate limit.

**Matching a Google Places listing** (practical advice, not sourced). Normalise both names (uppercase; strip `LTD/LIMITED/PLC/LLP`, `THE`, punctuation; `&`→`AND`), search, then require a corroborator: registered-office postcode or town equals the listing's, or the SIC code fits the niche. Registered offices are often an accountant's, so a town mismatch lowers confidence rather than rejecting. Trading names are not in the register: "Dave's Plumbing" may be a sole trader while "DAVES PLUMBING LTD" exists elsewhere, so a name-only hit is a false-positive risk. Require `company_status == active` and `type` in `{ltd, plc, llp}`; treat "no match" as "probably unincorporated", not proof.

## 3. B2B cold calling: TPS and CTPS

Regulation 21: no unsolicited live marketing calls "to any number registered with the TPS or CTPS, unless the person has specifically consented" [S20]. The CTPS is the TPS "for companies and other corporate bodies (limited liability partnerships, Scottish partnerships and government bodies)" [S20]; "Sole traders and partnerships register on the TPS and not the CTPS" [S22]. For business calls, screen "against both the CTPS and TPS registers, as well as your own 'do not call' list" [S1]. Registrations take up to 28 days to activate, so a check older than 28 days may miss numbers [S21]. On every call display your number, say who is calling, and give a contact address or freephone number if asked [S20].

**Screening in practice.** A full TPS+CTPS licence is quoted by one bureau at about £3,300/year plus integration [S23] (official tpsonline.org.uk pages did not render for me; unverified). Small operators use a screening API: roughly £3–£5 per 1,000 combined checks, some with free tiers [S24]; tpsapi.com sells 5,000 credits for £40 [S25]. CTPS registration is free and renewed annually [S22].

## 4. SMS to a business mobile

Texts are "electronic mail" under PECR [S27], so the email rules apply: a corporate subscriber (including an employee's mobile at a corporate body) needs no consent; a sole trader or ordinary partnership needs consent or soft opt-in [S1, S2]. TPS/CTPS cover calls, not texts. Identity and opt-out ("reply STOP") in every message [S4, S6]. You cannot tell from a number whether it is a company line or a sole trader's personal phone [S4].

## 5. Letters with a QR code

"Direct marketing by post is not covered by PECR" [S26]. UK GDPR applies only if you use personal data: "If you're putting someone's name on a letter or flyer, you'll need a lawful basis"; legitimate interests is normally fine for post [S4, S26]. The Mailing Preference Service check is good practice, "not a statutory preference service" [S26]. A letter to "The Owner, [Business], [address]" uses no personal data for a company; for a sole trader the trading address may be personal data, so keep an LI assessment and put a privacy notice on the QR landing page. The landing page's cookies fall under PECR reg 6 (analytics cookies now consent-exempt) [S8].

## What this means for the pipeline

1. **Cold email only leads matched on Companies House as an active `ltd`, `plc` or `llp`** (corroborated by postcode/town). No match, or a sole trader / ordinary partnership: no cold email [S1, S2, S4, S5, S15].
2. **Same gate for SMS**; unmatched leads go to letter or a screened call, never email/SMS [S1, S27, S26].
3. **Before any call, screen the number against TPS + CTPS + our own do-not-call list within the last 28 days**; show CLI; say who is calling; store the check result and date on the lead [S1, S20, S21].
4. **Keep the footer**: real trading name, postal address, working unsubscribe/reply opt-out, in every email and text; suppress on any opt-out, corporate or not [S6, S3, S1].
5. **Prefer role addresses (info@, hello@) over named-person addresses.** If a named address is used: record the legitimate-interests assessment, link a privacy notice, honour objections promptly [S1, S7, S8].
6. **Do not treat a scraped or public email as consent** [S1].
7. **Log the Companies House `company_number`, `status`, `type` and match evidence** so the corporate-subscriber decision is auditable if the ICO asks (fines now up to £17.5m/4%) [S9].

**Where the law is uncertain.** (a) ICO guides are "under review" post-DUAA [S2]. (b) Employee addresses: PECR-clear per the B2B page [S1], but the mail guide only says you "may also need to consider data protection implications" [S2]; treat GDPR LI as mandatory. (c) No source gives a reliable way to prove a listing is a company; a Companies House match is evidence, not proof, and a false match on a generic name would put a sole trader in the email bucket. (d) Ordinary partnerships (including Scottish general partnerships) are not on the register; treat as individual subscribers absent other evidence. (e) Official TPS licence prices unverified.

## Sources (all accessed 2026-09-20)

- [S1] ICO, Business-to-business marketing — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/
- [S2] ICO, Guide to PECR: Electronic mail marketing — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/electronic-and-telephone-marketing/electronic-mail-marketing/
- [S3] ICO, How do we comply with the PECR electronic mail marketing rules? — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guidance-on-direct-marketing-using-electronic-mail/how-do-we-comply-with-the-pecr-electronic-mail-marketing-rules/
- [S4] ICO (small organisations), Marketing and data protection in detail — https://ico.org.uk/for-organisations/advice-for-small-organisations/direct-marketing-and-data-protection/marketing-and-data-protection-in-detail/
- [S5] PECR 2003 reg 22 (version 5 Feb 2026) — https://www.legislation.gov.uk/uksi/2003/2426/regulation/22
- [S6] PECR 2003 reg 23 — https://www.legislation.gov.uk/uksi/2003/2426/regulation/23
- [S7] ICO, Sending direct marketing: choosing your lawful basis — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/sending-direct-marketing-choosing-your-lawful-basis/
- [S8] ICO, The DUAA: what does it mean for organisations? — https://ico.org.uk/about-the-ico/what-we-do/legislation-we-cover/data-use-and-access-act-2025/the-data-use-and-access-act-2025-what-does-it-mean-for-organisations/
- [S9] ICO, Statement on the commencement of the DUAA (Feb 2026) — https://ico.org.uk/about-the-ico/media-centre/news-and-blogs/2026/02/statement-on-the-commencement-of-the-data-use-and-access-act-duaa/
- [S10] Clifford Chance, Key aspects of the DUAA take effect (Feb 2026) — https://www.cliffordchance.com/insights/resources/blogs/talking-tech/en/articles/2026/02/key-aspects-of-the-data--use-and-access--act-take-effect.html
- [S11] Companies House, Rate limiting — https://developer-specs.company-information.service.gov.uk/guides/rateLimiting
- [S12] Companies House, Search companies — https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference/search/search-companies
- [S13] Companies House, Advanced company search — https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference/search/advanced-company-search
- [S14] Companies House, CompanySearch resource — https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/companysearch?v=latest
- [S15] Companies House, CompanyProfile resource — https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/companyprofile?v=latest
- [S16] Companies House, Authorisation guide — https://developer-specs.company-information.service.gov.uk/guides/authorisation
- [S17] Companies House, How to create an application — https://developer.company-information.service.gov.uk/how-to-create-an-application
- [S18] Companies House, Free Company Data Product — https://download.companieshouse.gov.uk/en_output.html
- [S19] Paul Walsh, Companies House API gotchas — https://paul-walsh.co.uk/companies-house-api-gotchas/
- [S20] ICO, Guide to PECR: Telephone marketing — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/electronic-and-telephone-marketing/telephone-marketing/
- [S21] ICO, How do we comply with the rules on live marketing calls? — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guidance-on-direct-marketing-using-live-calls/how-do-we-comply-with-the-rules-on-live-marketing-calls/
- [S22] TPS Services, The Corporate Telephone Preference Service — https://www.tpsservices.co.uk/corporate-telephone-preference-service.aspx
- [S23] TPS Services, TPS licence vs bureau comparison — https://www.tpsservices.co.uk/tps-services-for-businesses/tps-licence.aspx
- [S24] TPSCheck, Best TPS check APIs in the UK (Jan 2026) — https://tpscheck.uk/blog/2026/1/27/best-tps-check-apis-uk/
- [S25] tpsapi.com pricing — https://tpsapi.com/
- [S26] ICO, Direct marketing guidance: Plan direct marketing — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/direct-marketing-guidance/plan-direct-marketing/
- [S27] ICO, Guide to PECR: Electronic and telephone marketing (overview) — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/electronic-and-telephone-marketing/
