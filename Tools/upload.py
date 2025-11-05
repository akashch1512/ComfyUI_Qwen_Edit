import os
import requests


def upload_to_imgbb(image_path, api_key: str | None = None) -> str:
    """Upload an image file to ImgBB and return the hosted URL or an error string.

    Notes:
    - If api_key is not provided, the function will read IMGBB_API_KEY from the
      environment.
    - The previous implementation called ``file.read()`` and then passed the
      same file object to requests which left the file pointer at EOF; that
      resulted in empty uploads. This version sends the file object directly
      in ``files`` without pre-reading it.
    """
    if api_key is None:
        api_key = os.getenv("IMGBB_API_KEY")
    if not api_key:
        return "Error: IMGBB API key not provided"

    url = "https://api.imgbb.com/1/upload"

    try:
        with open(image_path, "rb") as f:
            # Send as multipart/form-data. Provide a filename so the server
            # receives a proper filename in the part.
            files = {"image": (os.path.basename(image_path), f)}
            data = {"key": api_key}
            resp = requests.post(url, files=files, data=data, timeout=60)

        # Attempt to decode JSON response
        try:
            j = resp.json()
        except Exception:
            return f"Error: {resp.status_code} - {resp.text}"

        if resp.status_code == 200 and j.get("success"):
            return j["data"]["url"]
        # Prefer the API-provided error message if present
        error_obj = j.get("error") if isinstance(j, dict) else None
        if error_obj and isinstance(error_obj, dict):
            msg = error_obj.get("message") or str(error_obj)
        else:
            msg = str(j)
        return f"Error: {resp.status_code} - {msg}"
    except Exception as e:
        return f"Error: exception during upload: {e}"


# Example usage:
# api_key = "YOUR_IMGBB_API_KEY"
# image_path = "photo.jpg"
# print(upload_to_imgbb(api_key, image_path))
