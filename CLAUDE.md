# JCube phone edit workflow

Jesse records on his phone, uploads to Google Drive, then says "edit it" from the Claude app.

Drive folders (owner jesse@gaskinsdigital.com, shared "anyone with the link"):
* JCube Raw: `1Hs2bNU_zJSjFhpjwkMAmhZF5GEXjC4LR` (raw clips)
* JCube Assets: `1HpqreDY347O5ISBTJOeM_z2eyD3EN0Pp` (music, sfx, overlays)

When asked to edit:
1. Find the newest video: Google Drive `search_files` with
   `parentId = '1Hs2bNU_zJSjFhpjwkMAmhZF5GEXjC4LR' and mimeType contains 'video/'`, pick latest createdTime.
   Never use `download_file_content` for video, it's too big. Use `./fetch.sh <id> input/<name>` instead.
2. Pull assets once per session: list JCube Assets, fetch audio files into `Assets/music/` or `Assets/sfx/`
   (sfx if the name has whoosh, riser, boom or impact), `.webm` into `Assets/overlays/`.
3. Run `python edit.py input/<name>` with `--hook "..."` if Jesse gave one.
4. Look at a few frames to check captions and hook, then send `output/<name>_edit.mp4` with SendUserFile.
5. Keep replies short. Jesse is on his phone.

If this session's network blocks huggingface.co / drive.usercontent.google.com, render on GitHub Actions instead:
trigger workflow `edit.yml` (inputs file_id, name, hook) via the GitHub MCP, wait for it, then
`git fetch origin renders` and send `renders/<name>/<name>_edit.mp4` from that branch with SendUserFile.
