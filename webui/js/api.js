/**
 * Call a JSON-in JSON-out API endpoint
 * Data is automatically serialized
 * @param {string} endpoint - The API endpoint to call
 * @param {any} data - The data to send to the API
 * @returns {Promise<any>} The JSON response from the API
 */
export async function callJsonApi(endpoint, data) {
  const response = await fetchApi(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    credentials: "same-origin",
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(error);
  }
  const jsonResponse = await response.json();
  return jsonResponse;
}

/**
 * Detect the application base path for API calls
 */
function getApiBase() {
  const pathname = globalThis.location.pathname;
  return pathname.startsWith('/agi/') || pathname === '/agi' ? '/agi' : '';
}

/**
 * Fetch wrapper for Andy APIs that ensures token exchange
 * Automatically adds CSRF token to request headers
 * @param {string} url - The URL to fetch
 * @param {Object} [request] - The fetch request options
 * @returns {Promise<Response>} The fetch response
 */
export async function fetchApi(url, request) {
  const apiBase = getApiBase();
  // Ensure absolute paths start with the correct base (e.g. /agi)
  const finalUrl = (url.startsWith('/') && !url.startsWith(apiBase + '/')) ? (apiBase + url) : url;

  const startTime = Date.now();
  console.log(`[API_DEBUG] Request START: ${finalUrl}`);

  async function _wrap(retry) {
    // get the CSRF token
    const token = await getCsrfToken();

    // create a new request object if none was provided
    const finalRequest = request || {};

    // ensure headers object exists
    finalRequest.headers = finalRequest.headers || {};

    // default credentials to same-origin if not specified
    if (!finalRequest.credentials) {
      finalRequest.credentials = "same-origin";
    }

    // add the CSRF token to the headers
    finalRequest.headers["X-CSRF-Token"] = token;

    // perform the fetch with the updated request
    const response = await fetch(finalUrl, finalRequest);

    // check if there was an CSRF error
    if (response.status === 403 && retry) {
      console.warn(`[API_DEBUG] CSRF Error for ${finalUrl}, retrying...`);
      // retry the request with new token
      csrfToken = null;
      return await _wrap(false);
    } else if (response.redirected && response.url.endsWith("/login")) {
      console.warn(`[API_DEBUG] Redirected to login for ${finalUrl}`);
      // redirect to login
      window.location.href = response.url;
      return;
    }

    // return the response
    return response;
  }

  try {
    // perform the request
    const response = await _wrap(true);
    const duration = Date.now() - startTime;
    console.log(`[API_DEBUG] Request END: ${finalUrl} - Duration: ${duration}ms - Status: ${response?.status}`);

    if (duration > 5000) {
      console.error(`[API_DEBUG] SLOW REQUEST DETECTED: ${finalUrl} took ${duration}ms`);
    }

    // return the response
    return response;
  } catch (error) {
    const duration = Date.now() - startTime;
    console.error(`[API_DEBUG] Request FAILED: ${finalUrl} - Duration: ${duration}ms - Error:`, error);
    throw error;
  }
}

// csrf token stored locally
let csrfToken = null;

/**
 * Get the CSRF token for API requests
 * Caches the token after first request
 * @returns {Promise<string>} The CSRF token
 */
async function getCsrfToken() {
  if (csrfToken) return csrfToken;
  const baseUrl = getApiBase();
  const response = await fetch(baseUrl + "/csrf_token", {
    credentials: "same-origin",
  });
  if (response.redirected && response.url.endsWith("/login")) {
    // redirect to login
    window.location.href = response.url;
    return;
  }
  const json = await response.json();
  if (json.ok) {
    csrfToken = json.token;
    const isHttps = window.location.protocol === "https:";
    const cookieOptions = [
      `csrf_token_${json.runtime_id}=${csrfToken}`,
      "SameSite=Lax",
      "Path=/",
      isHttps ? "Secure" : ""
    ].filter(Boolean).join("; ");
    document.cookie = cookieOptions;
    return csrfToken;
  } else {
    if (json.error) alert(json.error);
    throw new Error(json.error || "Failed to get CSRF token");
  }
}
