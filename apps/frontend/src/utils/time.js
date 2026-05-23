export const IST_OFFSET_MINUTES = 330;

export function utcHourToIst(utcHour) {
  const total = (utcHour * 60) + IST_OFFSET_MINUTES;
  const h24 = ((Math.floor(total / 60) % 24) + 24) % 24;
  const m = ((total % 60) + 60) % 60;
  const ampm = h24 >= 12 ? 'PM' : 'AM';
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  return `${h12}:${m.toString().padStart(2, '0')} ${ampm} IST`;
}

export const UTC_MIDNIGHT_IN_IST = utcHourToIst(0);

export const TODAY_BUCKET_CAPTION = `Today = 00:00 UTC → now (${UTC_MIDNIGHT_IN_IST} → now IST)`;
