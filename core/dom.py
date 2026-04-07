def scrape_visible(page):
    return page.evaluate(r"""
() => {

function getId(article) {
    const a = article.querySelector('a[href*="/status/"]');
    if (!a) return "";
    return a.href.split("/status/")[1]?.split("?")[0] || "";
}

function getLinks(scope) {
    if (!scope) return [];
    const seen = new Set();
    const links = [];
    for (const a of scope.querySelectorAll('a[href]')) {
        const h = a.href;
        if (!h.startsWith("http") || h.includes("/status/") || seen.has(h)) continue;
        seen.add(h);
        links.push(h);
    }
    for (const m of (scope.innerText || "").matchAll(/https:\/\/\n([^\s]+)/gu)) {
        const h = "https://" + m[1];
        if (!seen.has(h)) { seen.add(h); links.push(h); }
    }
    return links;
}

return [...document.querySelectorAll('article[data-testid="tweet"]')]
.map(article => {
    const textEl  = article.querySelector('[data-testid="tweetText"]');
    const linkEl  = article.querySelector('a[href*="/status/"]');
    const timeEl  = article.querySelector("time");
    const userEl  = article.querySelector('[data-testid="User-Name"]');
    const quoteEl = article.querySelector('[data-testid="quoteTweet"]');

    const quoteTextEl = quoteEl?.querySelector('[data-testid="tweetText"]');
    const quoteLinkEl = quoteEl?.querySelector('a[href*="/status/"]');
    const quoteTimeEl = quoteEl?.querySelector("time");
    const quoteUserEl = quoteEl?.querySelector('[data-testid="User-Name"]');

    let outerScope = article;
    if (quoteEl) {
        const clone = article.cloneNode(true);
        clone.querySelector('[data-testid="quoteTweet"]')?.remove();
        outerScope = clone;
    }

    return {
        tweetId:   getId(article),
        url:       linkEl ? linkEl.href : "",
        text:      textEl ? textEl.innerText : "",
        author:    userEl ? userEl.innerText : "",
        timestamp: timeEl ? timeEl.getAttribute("datetime") : "",
        domLinks:  getLinks(outerScope.querySelector('[data-testid="tweetText"]')),
        quote: quoteEl ? {
            tweetId:   quoteLinkEl ? quoteLinkEl.href.split("/status/")[1]?.split("?")[0] : "",
            url:       quoteLinkEl ? quoteLinkEl.href : "",
            text:      quoteTextEl ? quoteTextEl.innerText : "",
            author:    quoteUserEl ? quoteUserEl.innerText : "",
            timestamp: quoteTimeEl ? quoteTimeEl.getAttribute("datetime") : "",
            domLinks:  getLinks(quoteTextEl),
        } : null,
    };
});

}
""")
