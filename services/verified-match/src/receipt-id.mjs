const RECEIPT_ID = /^[A-Za-z0-9._:-]{1,128}$/;

export function isValidReceiptId(value) {
  return typeof value === "string" && RECEIPT_ID.test(value);
}

export function receiptIdFrom(receipt) {
  if (!receipt || typeof receipt !== "object") return null;
  const explicit = receipt.receipt_id;
  const matchId = receipt.match && typeof receipt.match === "object" ? receipt.match.match_id : null;
  const id = typeof explicit === "string" && explicit.length > 0 ? explicit : matchId;
  return isValidReceiptId(id) ? id : null;
}
