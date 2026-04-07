import browser_cookie3
from config import CHROME_PROFILE


def get_x_cookies():
    print("Extracting cookies from Chrome...")
    cookies = []
    seen = set()
    for domain in [".x.com", "x.com", ".twitter.com", "twitter.com"]:
        try:
            jar = browser_cookie3.chrome(
                domain_name=domain,
                cookie_file=str(CHROME_PROFILE / "Cookies"),
            )
            for c in jar:
                key = (c.name, c.domain)
                if key not in seen:
                    seen.add(key)
                    cookies.append({
                        "name": c.name,
                        "value": c.value,
                        "domain": c.domain,
                        "path": c.path,
                        "secure": bool(c.secure),
                        "httpOnly": False,
                        "sameSite": "Lax",
                    })
        except Exception as e:
            print(f"  {domain}: {e}")
    print(f"Total cookies: {len(cookies)}")
    auth = [c for c in cookies if c["name"] in ("auth_token", "ct0")]
    if not auth:
        print("WARNING: no auth_token or ct0 found")
    return cookies
