IMPORTANT: This package adds the separate profile page and front-end photo upload UI.
The photo upload UI expects these authenticated backend endpoints:
  POST   /donors/me/photo   multipart field name: photo
  DELETE /donors/me/photo
It also expects GET /donors/me to include profile_photo_url.

Do NOT replace your existing main.py or database.py with a guessed full file.
Your current uploaded backend/database.py has a COLUMN_ADDITIONS list. Add this entry:
  ("donor_accounts", "profile_photo_path", "VARCHAR(500) NULL"),

Then merge the following backend additions into your CURRENT main.py (preserve your existing profile PUT and password routes):

1) Extend the FastAPI import:
  from fastapi import UploadFile, File
  from fastapi.staticfiles import StaticFiles
  import uuid

2) After BASE_DIR is defined, add:
  PROFILE_UPLOAD_DIR = BASE_DIR / "uploads" / "donor_profiles"
  PROFILE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
  app.mount("/uploads", StaticFiles(directory=BASE_DIR / "uploads"), name="uploads")

3) Ensure GET /donors/me returns the profile_photo_path column. If it returns the donor dependency object directly, add a query for profile_photo_path and return profile_photo_url as:
  profile_photo_url = f"/uploads/donor_profiles/{Path(row['profile_photo_path']).name}" if row and row.get("profile_photo_path") else None

4) Add these endpoints below GET /donors/me. They use the existing get_current_donor, fetch_one and execute_query helpers:

  ALLOWED_PHOTO_TYPES = {
      "image/jpeg": (".jpg", b"\xff\xd8\xff"),
      "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
      "image/webp": (".webp", b"RIFF"),
  }

  @app.post("/donors/me/photo")
  async def upload_my_profile_photo(photo: UploadFile = File(...), donor=Depends(get_current_donor)):
      content_type = photo.content_type or ""
      spec = ALLOWED_PHOTO_TYPES.get(content_type)
      if not spec:
          raise HTTPException(status_code=415, detail="Upload a JPG, PNG or WEBP image.")
      content = await photo.read(2 * 1024 * 1024 + 1)
      if len(content) > 2 * 1024 * 1024:
          raise HTTPException(status_code=413, detail="Photo must be 2 MB or smaller.")
      if not content.startswith(spec[1]) or (content_type == "image/webp" and content[8:12] != b"WEBP"):
          raise HTTPException(status_code=415, detail="The uploaded file is not a valid image of the selected type.")
      old = fetch_one("SELECT profile_photo_path FROM donor_accounts WHERE id = %s", (donor["id"],))
      filename = f"donor_{donor['id']}_{uuid.uuid4().hex}{spec[0]}"
      destination = PROFILE_UPLOAD_DIR / filename
      destination.write_bytes(content)
      execute_query("UPDATE donor_accounts SET profile_photo_path = %s WHERE id = %s", (str(Path("donor_profiles") / filename), donor["id"]))
      if old and old.get("profile_photo_path"):
          old_path = BASE_DIR / "uploads" / old["profile_photo_path"]
          if old_path.is_file(): old_path.unlink()
      return {"message": "Profile photo uploaded successfully.", "profile_photo_url": f"/uploads/donor_profiles/{filename}"}

  @app.delete("/donors/me/photo")
  def delete_my_profile_photo(donor=Depends(get_current_donor)):
      row = fetch_one("SELECT profile_photo_path FROM donor_accounts WHERE id = %s", (donor["id"],))
      execute_query("UPDATE donor_accounts SET profile_photo_path = NULL WHERE id = %s", (donor["id"],))
      if row and row.get("profile_photo_path"):
          old_path = BASE_DIR / "uploads" / row["profile_photo_path"]
          if old_path.is_file(): old_path.unlink()
      return {"message": "Profile photo removed."}

NOTE: Run the API after updating database.py so initialize_database() adds the new nullable column.
Also ensure python-multipart is installed (required by FastAPI UploadFile/File):
  pip install python-multipart
