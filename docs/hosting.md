# Host with OnlyMap

Every exported map carries a **Host with OnlyMap** link in its bottom-right
credit component. Hosting turns the file you already checked into a shareable
link, without re-exporting anything.

## Nothing uploads on its own

An exported map is a file on your disk. Opening it makes no network request and
sends nothing anywhere — the **Host** link is a link, not an upload. It opens
the OnlyMap hosting page and asks you to choose the exported file yourself.

This is deliberate. A page opened from disk cannot honestly ask for consent on
your behalf, so it does not try.

## What you are asked before anything is published

> You are about to upload a copy of this map and its included data. Confirm that
> you are authorised to publish it and choose Public, Unlisted, or Private
> visibility.

Two things worth reading carefully:

**The data goes with the map.** A Standalone HTML has every feature embedded in
it. Publishing it publishes the attributes too, including any column you left in
because it was convenient. Check the popup field list on the **Layers** tab
before you host.

**Authorisation is yours to confirm.** Licence terms on source data usually
distinguish between analysing it and republishing it. The plugin cannot know
which of your layers are yours to publish.

## Visibility

| Choice | Who can open it |
|---|---|
| **Public** | Anyone, and it may be indexed by search engines |
| **Unlisted** | Anyone holding the link |
| **Private** | You, and accounts you grant access to |

## Publishing from inside QGIS

Not in `0.1.0`. A **Publish with OnlyMap** action in the export dialog is
planned, and it will be a separate, explicit step after account sign-in with the
same data-inventory confirmation — never a side effect of exporting.

For now: export locally, open the map, check it, then use the **Host** link.

## Hosting somewhere else

The export is a plain HTML file with no server requirement, so any static host
works — GitHub Pages, S3, Netlify, or a folder on a web server. Choose the
**Folder** output mode if you would rather copy a directory than a single file.

Nothing in the artifact phones home to NIKA, whether you host with OnlyMap or
not.
