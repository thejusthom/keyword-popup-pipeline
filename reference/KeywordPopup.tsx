"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { createPortal } from "react-dom";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface KeywordData {
  term: string;
  definition: string;
  category: string;
}

interface FullArticle {
  term: string;
  definition: string;
  full_text: string;
  category: string;
  source_url: string;
}

interface KeywordPopupProps {
  slug: string;
  children: React.ReactNode;
}

// ---------------------------------------------------------------------------
// In-memory cache
// ---------------------------------------------------------------------------

const popupCache = new Map<string, KeywordData | null>();
const articleCache = new Map<string, FullArticle | null>();

async function fetchKeyword(slug: string): Promise<KeywordData | null> {
  if (popupCache.has(slug)) return popupCache.get(slug)!;

  try {
    const res = await fetch(`/api/keyword/${slug}`);
    if (!res.ok) {
      popupCache.set(slug, null);
      return null;
    }
    const data: KeywordData = await res.json();
    popupCache.set(slug, data);
    return data;
  } catch {
    popupCache.set(slug, null);
    return null;
  }
}

async function fetchFullArticle(slug: string): Promise<FullArticle | null> {
  if (articleCache.has(slug)) return articleCache.get(slug)!;

  try {
    const res = await fetch(`/api/keyword/${slug}?full=1`);
    if (!res.ok) {
      articleCache.set(slug, null);
      return null;
    }
    const data: FullArticle = await res.json();
    articleCache.set(slug, data);
    return data;
  } catch {
    articleCache.set(slug, null);
    return null;
  }
}

// ---------------------------------------------------------------------------
// Article Modal (rendered via portal)
// ---------------------------------------------------------------------------

function ArticleModal({
  slug,
  onClose,
}: {
  slug: string;
  onClose: () => void;
}) {
  const [article, setArticle] = useState<FullArticle | null>(null);
  const [loading, setLoading] = useState(true);
  const backdropRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;

    fetchFullArticle(slug).then((data) => {
      if (!cancelled) {
        setArticle(data);
        setLoading(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [slug]);

  // Close on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  // Lock body scroll
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  // Close on backdrop click
  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === backdropRef.current) onClose();
  };

  const paragraphs = article
    ? (article.full_text || article.definition)
        .split(/\n{2,}/)
        .filter((p) => p.trim().length > 0)
    : [];

  return (
    <div
      ref={backdropRef}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-[100] flex items-start justify-center overflow-y-auto bg-black/50 backdrop-blur-sm"
      style={{ padding: "5vh 1rem" }}
    >
      <div
        className="relative w-full max-w-2xl rounded-2xl border border-fd-border bg-fd-card shadow-2xl"
        data-keyword-modal
      >
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-lg text-fd-muted-foreground transition-colors hover:bg-fd-accent hover:text-fd-accent-foreground"
          aria-label="Close"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path
              d="M4 4l8 8M12 4l-8 8"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        </button>

        {loading ? (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="mb-4 h-8 w-8 animate-spin rounded-full border-2 border-fd-border border-t-fd-primary" />
            <span className="text-sm text-fd-muted-foreground">Loading article...</span>
          </div>
        ) : !article ? (
          <div className="flex flex-col items-center justify-center py-20">
            <span className="text-sm text-fd-muted-foreground">Article not found.</span>
          </div>
        ) : (
          <>
            {/* Header */}
            <div className="border-b border-fd-border px-8 pb-6 pt-8">
              <span className="mb-2 inline-block rounded-md bg-teal-700/10 px-2.5 py-1 text-xs font-medium text-teal-700 dark:bg-teal-400/10 dark:text-teal-400">
                {article.category}
              </span>
              <div className="text-2xl font-bold text-fd-foreground">
                {article.term}
              </div>
            </div>

            {/* Body */}
            <div className="max-h-[60vh] overflow-y-auto px-8 py-6">
              <div className="space-y-4">
                {paragraphs.map((para, i) => (
                  <div
                    key={i}
                    className="text-[15px] leading-relaxed text-fd-muted-foreground"
                  >
                    {para}
                  </div>
                ))}
              </div>
            </div>

            {/* Footer */}
            <div className="border-t border-fd-border px-8 py-4">
              <span className="text-[12px] text-fd-muted-foreground">
                Source: Wikipedia · Medhavi Knowledge Base
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Popup skeleton — shown instantly on click while fetch is in flight
// Uses getClientRects() to match PopupCard positioning on wrapped keywords
// ---------------------------------------------------------------------------

function PopupSkeleton({ anchor }: { anchor: HTMLElement }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({
    top: 0,
    left: 0,
    placeBelow: false,
    ready: false,
  });

  useEffect(() => {
    if (!ref.current) return;

    const rects = anchor.getClientRects();
    const aRect = rects.length > 0 ? rects[0] : anchor.getBoundingClientRect();

    const pRect = ref.current.getBoundingClientRect();
    const scrollY = window.scrollY;
    const scrollX = window.scrollX;

    let top = aRect.top + scrollY - pRect.height - 10;
    let placeBelow = false;
    if (top < scrollY + 8) {
      top = aRect.bottom + scrollY + 10;
      placeBelow = true;
    }

    let left = aRect.left + scrollX + aRect.width / 2 - pRect.width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - pRect.width - 8));

    setPos({ top, left, placeBelow, ready: true });
  }, [anchor]);

  return (
    <div
      ref={ref}
      data-keyword-popup
      style={{
        position: "absolute",
        top: pos.top,
        left: pos.left,
        zIndex: 50,
        opacity: pos.ready ? 1 : 0,
      }}
      className="w-[340px] rounded-xl border border-fd-border bg-fd-card shadow-sm"
    >
      <div className="p-4 space-y-2.5">
        <div className="h-4 w-20 rounded bg-fd-muted animate-pulse" />
        <div className="h-4 w-32 rounded bg-fd-muted animate-pulse" />
        <div className="space-y-1.5">
          <div className="h-3 w-full rounded bg-fd-muted animate-pulse" />
          <div className="h-3 w-full rounded bg-fd-muted animate-pulse" />
          <div className="h-3 w-2/3 rounded bg-fd-muted animate-pulse" />
        </div>
      </div>
      <div className="border-t border-fd-border px-4 py-2">
        <div className="h-3 w-24 rounded bg-fd-muted animate-pulse" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Popup card (rendered via portal)
// ---------------------------------------------------------------------------

function PopupCard({
  data,
  slug,
  anchor,
  onReadMore,
}: {
  data: KeywordData;
  slug: string;
  anchor: HTMLElement;
  onReadMore: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({
    top: 0,
    left: 0,
    placeBelow: false,
    ready: false,
  });

  useEffect(() => {
    if (!ref.current) return;

    const rects = anchor.getClientRects();
    const aRect = rects.length > 0 ? rects[0] : anchor.getBoundingClientRect();

    const pRect = ref.current.getBoundingClientRect();
    const scrollY = window.scrollY;
    const scrollX = window.scrollX;

    let top = aRect.top + scrollY - pRect.height - 10;
    let placeBelow = false;
    if (top < scrollY + 8) {
      top = aRect.bottom + scrollY + 10;
      placeBelow = true;
    }

    let left = aRect.left + scrollX + aRect.width / 2 - pRect.width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - pRect.width - 8));

    setPos({ top, left, placeBelow, ready: true });
  }, [anchor]);

  return (
    <div
      ref={ref}
      data-keyword-popup
      style={{
        position: "absolute",
        top: pos.top,
        left: pos.left,
        zIndex: 50,
        opacity: pos.ready ? 1 : 0,
      }}
      className="w-[340px] rounded-xl border border-fd-border bg-fd-card shadow-sm"
    >
      <div className="p-4">
        <span className="mb-1.5 inline-block rounded-md bg-teal-700/10 px-2 py-0.5 text-xs text-teal-700 dark:bg-teal-400/10 dark:text-teal-400">
          {data.category}
        </span>
        <span className="mb-1 block text-[15px] font-medium text-fd-foreground">
          {data.term}
        </span>
        <span className="block text-[13px] leading-relaxed text-fd-muted-foreground">
          {data.definition.split(/(?<=[.!?])\s+/).slice(0, 3).join(" ")}
        </span>
      </div>

      <div className="flex items-center justify-between border-t border-fd-border px-4 py-2">
        <span className="text-[11px] text-fd-muted-foreground">
          Medhavi Knowledge Base
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onReadMore();
          }}
          className="text-[12px] font-medium text-fd-primary hover:underline"
        >
          Read more →
        </button>
      </div>

      <div
        className="absolute left-1/2 h-2.5 w-2.5 -translate-x-1/2 rotate-45 border-fd-border bg-fd-card"
        style={
          pos.placeBelow
            ? { top: -5, borderTop: "1px solid", borderLeft: "1px solid" }
            : {
                bottom: -5,
                borderBottom: "1px solid",
                borderRight: "1px solid",
              }
        }
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function KeywordPopup({ slug, children }: KeywordPopupProps) {
  const spanRef = useRef<HTMLSpanElement>(null);
  const [data, setData] = useState<KeywordData | null>(null);
  const [visible, setVisible] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Wait for client mount before rendering portals
  useEffect(() => {
    setMounted(true);
  }, []);

  // Hover prefetch with 100ms debounce — fires before the user clicks,
  // so data is usually cached by the time the popup opens.
  const handleMouseEnter = useCallback(() => {
    if (data) return; // already cached
    hoverTimerRef.current = setTimeout(() => {
      fetchKeyword(slug).then((result) => {
        if (result) setData(result);
      });
    }, 100);
  }, [slug, data]);

  const handleMouseLeave = useCallback(() => {
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
  }, []);

  // Show popup immediately on click (skeleton if data not ready yet).
  // If fetch fails, hide again.
  const toggle = useCallback(async () => {
    if (visible) {
      setVisible(false);
      return;
    }
    setVisible(true);
    if (!data) {
      const result = await fetchKeyword(slug);
      if (result) setData(result);
      else setVisible(false);
    }
  }, [slug, data, visible]);

  const openModal = useCallback(() => {
    setVisible(false);
    setModalOpen(true);
  }, []);

  const closeModal = useCallback(() => {
    setModalOpen(false);
  }, []);

  // Close popup when clicking outside
  useEffect(() => {
    if (!visible) return;
    const handleClick = (e: MouseEvent) => {
      if (
        spanRef.current &&
        !spanRef.current.contains(e.target as Node) &&
        !(e.target as HTMLElement).closest("[data-keyword-popup]")
      ) {
        setVisible(false);
      }
    };
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, [visible]);

  return (
    <>
      <span
        ref={spanRef}
        onClick={toggle}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        className="cursor-pointer text-fd-primary underline decoration-dotted underline-offset-[3px] hover:decoration-solid"
      >
        {children}
      </span>

      {mounted && visible && spanRef.current && (
        data
          ? createPortal(
              <PopupCard
                data={data}
                slug={slug}
                anchor={spanRef.current}
                onReadMore={openModal}
              />,
              document.body
            )
          : createPortal(
              <PopupSkeleton anchor={spanRef.current} />,
              document.body
            )
      )}

      {mounted &&
        modalOpen &&
        createPortal(<ArticleModal slug={slug} onClose={closeModal} />, document.body)}
    </>
  );
}
