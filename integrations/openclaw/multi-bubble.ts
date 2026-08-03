export type MultiBubbleOptions = {
  minChars: number;
  maxChars: number;
  maxBubbles: number;
};

const DEFAULT_OPTIONS: MultiBubbleOptions = {
  minChars: 24,
  maxChars: 360,
  maxBubbles: 3,
};

const STRUCTURED_MARKERS = [
  "```",
  "|---",
  "\n# ",
  "\n## ",
  "\n- ",
  "\n* ",
  "\n1. ",
  "http://",
  "https://",
];

function cleanPart(value: string): string {
  return value.replace(/[ \t]+\n/g, "\n").replace(/\n[ \t]+/g, "\n").trim();
}

function isStructured(text: string): boolean {
  if (STRUCTURED_MARKERS.some((marker) => text.includes(marker))) return true;
  if (/^\s*(?:[-*]|\d+[.)])\s+/m.test(text)) return true;
  if (/^\s*#{1,6}\s+/m.test(text)) return true;
  return false;
}

function sentenceParts(text: string): string[] {
  const matches = text.match(
    /[^。！？!?]+[。！？!?]+(?:[\p{Extended_Pictographic}\uFE0F\u200D～~✨🥰💗🌟（）()]{0,24})?|[^。！？!?]+$/gu,
  );
  return (matches || [text]).map(cleanPart).filter(Boolean);
}

function mergeTiny(parts: string[], minimum = 5): string[] {
  const result: string[] = [];
  for (const part of parts) {
    if (part.length < minimum && result.length > 0) {
      result[result.length - 1] = `${result[result.length - 1]}${part}`;
      continue;
    }
    result.push(part);
  }
  if (result.length > 1 && result[0].length < minimum) {
    result[1] = `${result[0]}${result[1]}`;
    result.shift();
  }
  return result;
}

function capParts(parts: string[], maxBubbles: number): string[] {
  if (parts.length <= maxBubbles) return parts;
  const head = parts.slice(0, maxBubbles - 1);
  const tail = parts.slice(maxBubbles - 1).join("");
  return [...head, tail];
}

export function splitNaturalBubbles(
  rawText: string,
  options: Partial<MultiBubbleOptions> = {},
): string[] {
  const config = { ...DEFAULT_OPTIONS, ...options };
  const text = cleanPart(rawText);
  if (
    !text ||
    text.length < config.minChars ||
    text.length > config.maxChars ||
    config.maxBubbles < 2 ||
    isStructured(text)
  ) {
    return text ? [text] : [];
  }

  const paragraphs = text
    .split(/\n\s*\n+/)
    .map(cleanPart)
    .filter(Boolean);
  let parts = paragraphs.length >= 2 ? paragraphs : sentenceParts(text);
  parts = mergeTiny(parts);
  parts = capParts(parts, Math.min(4, Math.max(2, config.maxBubbles)));

  if (parts.length < 2) return [text];
  if (parts.some((part) => part.length < 4)) return [text];
  return parts;
}

export function shouldUseMultipleBubbles(
  rawText: string,
  options: Partial<MultiBubbleOptions> = {},
): boolean {
  return splitNaturalBubbles(rawText, options).length > 1;
}
