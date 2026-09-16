# Client photos and logo: the playbook

Written for the agents (human or AI) who operate this pipeline. It covers the
one interaction that most often decides whether a prospect buys: getting
their own photos and logo onto the preview. The mechanics are automated; this
document is about handling the conversation and the exceptions well.

## Why it matters

The preview is built from stock photos chosen for the trade. They look right,
but an owner knows they are not their van, their shopfront or their team.
The moment their own photos and logo are on the page it stops being "a
template" and becomes "my website". Conversion follows. So we:

1. **Offer it in the first email**, plainly, as included at no extra cost.
2. **Make it effortless**: they attach files to a reply, nothing else.
3. **Turn it round fast**: the preview is rebuilt automatically and they get a
   link back within the hour.

## What is automated

| Step | Where |
|---|---|
| Offer line in every cold email ("attach them to your reply") | `sales_agent._closing_paragraphs` |
| Negotiation replies know about the offer and what is already on file | `sales_agent._build_negotiation_prompt` |
| Image attachments on any inbound reply are saved and normalised | `email_utils._extract_attachments` -> `utils/assets.py` |
| Preview rebuilt in place (same URL) with logo in the nav and their photos in the hero and gallery | `design_agent.rebuild_preview` |
| Confirmation reply with the link | `sales_agent._maybe_apply_client_assets` |
| Manual rebuild after adding files by hand | `python run.py rebuild <lead_id>` |

Assets live in `pipeline/assets/<lead_id>/` as `logo.png`/`logo.svg` and
`photo-1.jpg` ... `photo-6.jpg`. They are pushed into the site's GitHub repo
and Vercel deployment, so a handed-over site keeps its images with no
dependency on our servers.

## What the agent handles

### When a prospect asks "can I use my own photos / logo?"

Yes, always, included in the price. Ask for:

- **Logo**: PNG with a transparent background, or SVG. A photo of a van with
  the logo on it is not a logo; ask if they have "the file the sign-maker or
  printer used".
- **Photos**: three to six, landscape (wider than tall), taken on a modern
  phone is fine. Their premises, their work, their team. At least one that
  would look good as the big picture at the top.
- **Copyright**: photos they took or paid for. Not pictures saved from a
  supplier's site or Google. If in doubt, ask "did you take these?".

Tell them to attach the files to a reply to our email. Do not point them to
Dropbox links or forms; a reply is the path the automation watches.

### When files arrive some other way (WhatsApp, a call, a USB stick)

1. Save them into `pipeline/assets/<lead_id>/`. Name the logo `logo.png` or
   `logo.svg`; photos as `photo-1.jpg`, `photo-2.jpg` and so on, in the order
   you want them shown (photo-1 is the hero).
2. Run `python run.py rebuild <lead_id>`.
3. Reply to the prospect with the preview link from the command's output.

### When the automated rebuild fails

A Slack alert and a console line say so. Usually one of: a file that is not
really an image (a PDF, a Word doc), a photo under 500 px, or a deploy hiccup.
Fix the files in the assets folder if needed and run the rebuild command.

### What not to do

- Do not touch up, crop or "improve" a client's logo without asking. It is
  their brand.
- Do not add photos of other businesses, or stock photos presented as theirs.
- Do not promise photography, retouching or brand design. We swap in what
  they send. If they want more than that, it is a separate conversation and
  a human quotes it.
- Do not chase for photos before they have said yes to the site. The offer
  is a reason to say yes, not a hurdle before it.

## Wording that works

> "Yes, absolutely, and it's included. If you attach your logo (PNG or SVG
> is ideal) and three to six photos of the place or the work to a reply,
> I'll have them on the preview within the hour and send you the link."

> "Got them, thanks. They're on the preview now: <link>. Say the word if
> you'd rather a different photo at the top."

## Limits (by design)

- Up to 6 photos; extra ones are ignored. One logo; a newer one replaces it.
- Photos are resized to 1600 px on the long edge and saved as JPEG so the
  site stays fast. Logos are kept as PNG (transparency) up to 600 px.
- Tiny images (email signature badges) are ignored so a normal reply doesn't
  trigger a rebuild.
