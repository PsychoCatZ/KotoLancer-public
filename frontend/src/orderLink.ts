export function orderFromUrl(url: string): string | null {
  const values = new URL(url).searchParams.getAll("order");
  return values.length === 1 &&
    /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(
      values[0],
    )
    ? values[0]
    : null;
}

export function urlWithOrder(url: string, order: string | null): string {
  const target = new URL(url);
  target.searchParams.delete("order");
  if (order) target.searchParams.set("order", order);
  return target.href;
}
