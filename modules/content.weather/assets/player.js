// vds-content-weather : contrat packages/sdk-ts/src/content.ts (CONTENT_API_VERSION 1).
// Données : flux normalisé par le serveur (`ctx.feed`), jamais d'appel externe depuis le player.
// Hors ligne : dernier état connu tant qu'il a moins de `max_age_s` ; au-delà, ou sans donnée, l'élément
// échoue (`feed_stale` / `feed_unavailable`) et le player le saute : rien d'inachevé ni de technique au public.
// Pictogrammes : MET Norway weathericons (MIT, voir ICONS-LICENSE-MIT.txt), régénérés par tools/build-icons.mjs.

const TEXT = {
  fr: { attribution: "Données MET Norway" },
  en: { attribution: "Data from MET Norway" },
};
const BANNER_RATIO = 2.6;

// <icons>
const PARTS = {
  "sun": "<symbol id=\"sun\"><path class=\"sun-glow\" fill=\"url(#sun-glow-grad)\" d=\"M66.64,47.86,82,41,66.64,34.12l9.84-13.66L59.76,22.22,61.46,5.47l-13.6,9.89L41,0,34.12,15.36,20.46,5.52l1.76,16.72L5.47,20.54l9.89,13.6L0,41l15.36,6.83L5.52,61.54l16.72-1.76L20.54,76.53l13.6-9.89L41,82l6.83-15.36,13.66,9.84L59.78,59.76l16.75,1.69Z\"></path><path class=\"sun-outer\" fill=\"#ffd348\" d=\"M19.28,53.5a25,25,0,1,0,9.15-34.16A25,25,0,0,0,19.28,53.5Z\"></path><path class=\"sun-inner\" fill=\"url(#sun-inner-grad)\" d=\"M22.74,51.5a21,21,0,1,0,7.69-28.69A21,21,0,0,0,22.74,51.5Z\"></path></symbol>",
  "sun-glow-grad": "<radialGradient id=\"sun-glow-grad\" cx=\"41\" cy=\"41\" r=\"41\" gradientUnits=\"userSpaceOnUse\"><stop offset=\"54%\" stop-color=\"#d6b849\"/><stop offset=\"67%\" stop-color=\"#ffce47\"/><stop offset=\"100%\" stop-color=\"#ffdb73\"/></radialGradient>",
  "sun-inner-grad": "<linearGradient id=\"sun-inner-grad\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"100%\"><stop offset=\"0%\" stop-color=\"#ffaf22\" /><stop offset=\"100%\" stop-color=\"#f09900\" /></linearGradient>",
  "s01d": "<symbol id=\"s01d\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(9,9) scale(1,1)\"></use></symbol>",
  "moon": "<symbol id=\"moon\"><path d=\"M28.43,0A28.44,28.44,0,0,1,32.3,14.32,28.61,28.61,0,0,1,3.69,42.93,28.71,28.71,0,0,1,0,42.66,28.59,28.59,0,1,0,28.43,0Z\" fill=\"url(#moon-grad)\"></path></symbol>",
  "moon-grad": "<linearGradient id=\"moon-grad\" x1=\"0%\" y1=\"50%\" x2=\"100%\" y2=\"0%\"><stop offset=\"0%\" stop-color=\"#686e73\" /><stop offset=\"100%\" stop-color=\"#6a7075\" /></linearGradient>",
  "s01n": "<symbol id=\"s01n\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(20,20) scale(1,1)\"></use></symbol>",
  "cloud": "<symbol id=\"cloud\"><path d=\"M55.7,5A23.94,23.94,0,0,0,34.37,18.05a9.9,9.9,0,0,0-12.78,5.56,15,15,0,0,0-1.71-.1A14.81,14.81,0,0,0,9.2,28,14.63,14.63,0,0,0,5,38.17v.21A14.83,14.83,0,0,0,19.88,53.06H75.59a14.3,14.3,0,0,0,3.67-28.14A23.93,23.93,0,0,0,55.7,5Z\"></path></symbol>",
  "s04": "<symbol id=\"s04\"><use xlink:href=\"#cloud\" fill=\"#dddddd\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use></symbol>",
  "s02d": "<symbol id=\"s02d\"><g mask=\"url(#cloud_43_37_063_063_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(4,9) scale(1,1)\"></use></g><use xlink:href=\"#cloud\" fill=\"#dddddd\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(43,37) scale(0.63,0.63)\"></use></symbol>",
  "s02n": "<symbol id=\"s02n\"><g mask=\"url(#cloud_43_37_063_063_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(15,20) scale(1,1)\"></use></g><use xlink:href=\"#cloud\" fill=\"#dddddd\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(43,37) scale(0.63,0.63)\"></use></symbol>",
  "fog": "<symbol id=\"fog\"><g fill=\"#999999\"><path d=\"M88.7,3H14.3C13.6,3,13,2.3,13,1.5S13.6,0,14.3,0h74.4C89.4,0,90,0.7,90,1.5S89.4,3,88.7,3z\"></path><path d=\"M75.7,11H1.3C0.6,11,0,10.3,0,9.5S0.6,8,1.3,8h74.4C76.4,8,77,8.7,77,9.5S76.4,11,75.7,11z\"></path><path d=\"M86.7,19H12.3c-0.7,0-1.3-0.7-1.3-1.5s0.6-1.5,1.3-1.5h74.4c0.7,0,1.3,0.7,1.3,1.5S87.4,19,86.7,19z\"></path></g></symbol>",
  "s15": "<symbol id=\"s15\"><use xlink:href=\"#cloud\" fill=\"#dddddd\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use><use xlink:href=\"#fog\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,76) scale(1,1)\"></use></symbol>",
  "raindrop": "<symbol id=\"raindrop\"><path fill=\"#0062bf\" d=\"M2.5,13A2.5,2.5,0,0,1,.21,9.51l3.55-8a2.5,2.5,0,0,1,4.57,2l-3.55,8A2.5,2.5,0,0,1,2.5,13Z\"></path></symbol>",
  "s10": "<symbol id=\"s10\"><use xlink:href=\"#cloud\" fill=\"#999999\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(18,78) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(32,87) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(47,79) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(60,78) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(74,87) scale(1,1)\"></use></symbol>",
  "lightning": "<symbol id=\"lightning\"><polygon fill=\"#ffdd15\" points=\"19.6 23.42 12.74 20.39 15.55 5 5 24.49 12.08 27.51 7.49 45 19.6 23.42\"></polygon></symbol>",
  "s11": "<symbol id=\"s11\"><g mask=\"url(#lightning_37_51_1_1_4)\"><use xlink:href=\"#cloud\" fill=\"#999999\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use></g><use xlink:href=\"#lightning\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(37,51) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(18,78) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(29,87) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(55,79) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(68,78) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(82,87) scale(1,1)\"></use></symbol>",
  "s41d": "<symbol id=\"s41d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s10\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s41n": "<symbol id=\"s41n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s10\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s25d": "<symbol id=\"s25d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s11\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s25n": "<symbol id=\"s25n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s11\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "snowflake": "<symbol id=\"snowflake\"><path fill=\"#47c0e3\" d=\"M11.68,4.47H8.85L10.27,2A1.35,1.35,0,1,0,7.93.67L6.51,3.12,5.1.67A1.35,1.35,0,0,0,3.26.18,1.35,1.35,0,0,0,2.76,2L4.18,4.47H1.35a1.35,1.35,0,1,0,0,2.7H4.18L2.76,9.62a1.35,1.35,0,0,0,.49,1.84A1.39,1.39,0,0,0,5.1,11L6.51,8.52,7.93,11a1.35,1.35,0,1,0,2.34-1.35L8.85,7.17h2.83a1.35,1.35,0,1,0,0-2.7Z\"></path></symbol>",
  "s48": "<symbol id=\"s48\"><use xlink:href=\"#cloud\" fill=\"#999999\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(15,79) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(32,86) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(47,80) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(58,88) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(74,80) scale(1,1)\"></use></symbol>",
  "s32": "<symbol id=\"s32\"><g mask=\"url(#lightning_37_51_1_1_4)\"><use xlink:href=\"#cloud\" fill=\"#999999\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use></g><use xlink:href=\"#lightning\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(37,51) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(15,79) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(30,86) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(55,80) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(66,88) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(82,80) scale(1,1)\"></use></symbol>",
  "s43d": "<symbol id=\"s43d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s48\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s43n": "<symbol id=\"s43n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s48\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s27d": "<symbol id=\"s27d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s32\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s27n": "<symbol id=\"s27n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s32\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s50": "<symbol id=\"s50\"><use xlink:href=\"#cloud\" fill=\"#999999\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(15,79) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(29,88) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(44,79) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(58,88) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(72,79) scale(1,1)\"></use></symbol>",
  "s34": "<symbol id=\"s34\"><g mask=\"url(#lightning_37_51_1_1_4)\"><use xlink:href=\"#cloud\" fill=\"#999999\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use></g><use xlink:href=\"#lightning\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(37,51) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(13,79) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(27,88) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(55,79) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(69,88) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(83,79) scale(1,1)\"></use></symbol>",
  "s45d": "<symbol id=\"s45d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s50\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s45n": "<symbol id=\"s45n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s50\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s29d": "<symbol id=\"s29d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s34\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s29n": "<symbol id=\"s29n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s34\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s46": "<symbol id=\"s46\"><use xlink:href=\"#cloud\" fill=\"#cccccc\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(32,87) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(56,78) scale(1,1)\"></use></symbol>",
  "s30": "<symbol id=\"s30\"><g mask=\"url(#lightning_37_51_1_1_4)\"><use xlink:href=\"#cloud\" fill=\"#cccccc\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use></g><use xlink:href=\"#lightning\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(37,51) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(28,87) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(58,78) scale(1,1)\"></use></symbol>",
  "s40d": "<symbol id=\"s40d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s46\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s40n": "<symbol id=\"s40n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s46\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s24d": "<symbol id=\"s24d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s30\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s24n": "<symbol id=\"s24n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s30\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s47": "<symbol id=\"s47\"><use xlink:href=\"#cloud\" fill=\"#cccccc\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(29,88) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(60,79) scale(1,1)\"></use></symbol>",
  "s31": "<symbol id=\"s31\"><g mask=\"url(#lightning_37_51_1_1_4)\"><use xlink:href=\"#cloud\" fill=\"#cccccc\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use></g><use xlink:href=\"#lightning\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(37,51) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(26,88) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(58,79) scale(1,1)\"></use></symbol>",
  "s42d": "<symbol id=\"s42d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s47\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s42n": "<symbol id=\"s42n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s47\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s49": "<symbol id=\"s49\"><use xlink:href=\"#cloud\" fill=\"#cccccc\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(29,88) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(58,79) scale(1,1)\"></use></symbol>",
  "s33": "<symbol id=\"s33\"><g mask=\"url(#lightning_37_51_1_1_4)\"><use xlink:href=\"#cloud\" fill=\"#cccccc\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use></g><use xlink:href=\"#lightning\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(37,51) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(26,88) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(58,79) scale(1,1)\"></use></symbol>",
  "s44d": "<symbol id=\"s44d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s49\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s44n": "<symbol id=\"s44n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s49\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s26d": "<symbol id=\"s26d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s31\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s26n": "<symbol id=\"s26n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s31\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s28d": "<symbol id=\"s28d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s33\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s28n": "<symbol id=\"s28n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s33\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s03d": "<symbol id=\"s03d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s04\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s03n": "<symbol id=\"s03n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s04\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s09": "<symbol id=\"s09\"><use xlink:href=\"#cloud\" fill=\"#b2b2b2\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(32,78) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(45,87) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(60,78) scale(1,1)\"></use></symbol>",
  "s22": "<symbol id=\"s22\"><g mask=\"url(#lightning_37_51_1_1_4)\"><use xlink:href=\"#cloud\" fill=\"#b2b2b2\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use></g><use xlink:href=\"#lightning\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(37,51) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(29,78) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(50,87) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(65,78) scale(1,1)\"></use></symbol>",
  "s05d": "<symbol id=\"s05d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s09\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s05n": "<symbol id=\"s05n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s09\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s06d": "<symbol id=\"s06d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s22\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s06n": "<symbol id=\"s06n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s22\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s12": "<symbol id=\"s12\"><use xlink:href=\"#cloud\" fill=\"#b2b2b2\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(30,79) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(46,86) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(60,80) scale(1,1)\"></use></symbol>",
  "s23": "<symbol id=\"s23\"><g mask=\"url(#lightning_37_51_1_1_4)\"><use xlink:href=\"#cloud\" fill=\"#b2b2b2\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use></g><use xlink:href=\"#lightning\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(37,51) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(27,79) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(50,86) scale(1,1)\"></use><use xlink:href=\"#raindrop\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(64,80) scale(1,1)\"></use></symbol>",
  "s07d": "<symbol id=\"s07d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s12\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s07n": "<symbol id=\"s07n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s12\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s20d": "<symbol id=\"s20d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s23\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s20n": "<symbol id=\"s20n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s23\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s13": "<symbol id=\"s13\"><use xlink:href=\"#cloud\" fill=\"#b2b2b2\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(30,79) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(44,88) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(58,79) scale(1,1)\"></use></symbol>",
  "s14": "<symbol id=\"s14\"><g mask=\"url(#lightning_37_51_1_1_4)\"><use xlink:href=\"#cloud\" fill=\"#b2b2b2\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(3,18) scale(1,1)\"></use></g><use xlink:href=\"#lightning\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(37,51) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(26,79) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(52,88) scale(1,1)\"></use><use xlink:href=\"#snowflake\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(66,79) scale(1,1)\"></use></symbol>",
  "s08d": "<symbol id=\"s08d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s13\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s08n": "<symbol id=\"s08n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s13\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s21d": "<symbol id=\"s21d\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#sun\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(0,2) scale(0.7,0.7)\"></use></g><use xlink:href=\"#s14\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
  "s21n": "<symbol id=\"s21n\"><g mask=\"url(#cloud_3_18_1_1_5)\"><use xlink:href=\"#moon\" x=\"0\" y=\"0\" width=\"100\" height=\"100\" transform=\"translate(2,5) scale(0.714285714,0.714285714)\"></use></g><use xlink:href=\"#s14\" x=\"0\" y=\"0\" width=\"100\" height=\"100\"></use></symbol>",
};
// code -> [corps, identifiants de PARTS utilisés]
const ICONS = {
  "clearsky_day": ["<use xlink:href=\"#s01d\"></use>","sun sun-glow-grad sun-inner-grad s01d"],
  "clearsky_night": ["<use xlink:href=\"#s01n\"></use>","moon moon-grad s01n"],
  "cloudy": ["<use xlink:href=\"#s04\"></use>","cloud s04"],
  "fair_day": ["<defs><mask id=\"cloud_43_37_063_063_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(43,37) scale(0.63,0.63)\"></use></mask></defs><use xlink:href=\"#s02d\"></use>","cloud sun sun-glow-grad sun-inner-grad s02d"],
  "fair_night": ["<defs><mask id=\"cloud_43_37_063_063_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(43,37) scale(0.63,0.63)\"></use></mask></defs><use xlink:href=\"#s02n\"></use>","cloud moon moon-grad s02n"],
  "fog": ["<use xlink:href=\"#s15\"></use>","fog cloud s15"],
  "heavyrain": ["<use xlink:href=\"#s10\"></use>","raindrop cloud s10"],
  "heavyrainandthunder": ["<defs><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s11\"></use>","raindrop lightning cloud s11"],
  "heavyrainshowers_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s41d\"></use>","raindrop cloud s10 sun sun-glow-grad sun-inner-grad s41d"],
  "heavyrainshowers_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s41n\"></use>","raindrop cloud s10 moon moon-grad s41n"],
  "heavyrainshowersandthunder_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s25d\"></use>","raindrop lightning cloud s11 sun sun-glow-grad sun-inner-grad s25d"],
  "heavyrainshowersandthunder_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s25n\"></use>","raindrop lightning cloud s11 moon moon-grad s25n"],
  "heavysleet": ["<use xlink:href=\"#s48\"></use>","raindrop snowflake cloud s48"],
  "heavysleetandthunder": ["<defs><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s32\"></use>","raindrop snowflake lightning cloud s32"],
  "heavysleetshowers_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s43d\"></use>","raindrop snowflake cloud s48 sun sun-glow-grad sun-inner-grad s43d"],
  "heavysleetshowers_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s43n\"></use>","raindrop snowflake cloud s48 moon moon-grad s43n"],
  "heavysleetshowersandthunder_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s27d\"></use>","raindrop snowflake lightning cloud s32 sun sun-glow-grad sun-inner-grad s27d"],
  "heavysleetshowersandthunder_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s27n\"></use>","raindrop snowflake lightning cloud s32 moon moon-grad s27n"],
  "heavysnow": ["<use xlink:href=\"#s50\"></use>","snowflake cloud s50"],
  "heavysnowandthunder": ["<defs><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s34\"></use>","snowflake lightning cloud s34"],
  "heavysnowshowers_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s45d\"></use>","snowflake cloud s50 sun sun-glow-grad sun-inner-grad s45d"],
  "heavysnowshowers_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s45n\"></use>","snowflake cloud s50 moon moon-grad s45n"],
  "heavysnowshowersandthunder_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s29d\"></use>","snowflake lightning cloud s34 sun sun-glow-grad sun-inner-grad s29d"],
  "heavysnowshowersandthunder_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s29n\"></use>","snowflake lightning cloud s34 moon moon-grad s29n"],
  "lightrain": ["<use xlink:href=\"#s46\"></use>","raindrop cloud s46"],
  "lightrainandthunder": ["<defs><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s30\"></use>","raindrop lightning cloud s30"],
  "lightrainshowers_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s40d\"></use>","raindrop cloud s46 sun sun-glow-grad sun-inner-grad s40d"],
  "lightrainshowers_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s40n\"></use>","raindrop cloud s46 moon moon-grad s40n"],
  "lightrainshowersandthunder_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s24d\"></use>","raindrop lightning cloud s30 sun sun-glow-grad sun-inner-grad s24d"],
  "lightrainshowersandthunder_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s24n\"></use>","raindrop lightning cloud s30 moon moon-grad s24n"],
  "lightsleet": ["<use xlink:href=\"#s47\"></use>","raindrop snowflake cloud s47"],
  "lightsleetandthunder": ["<defs><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s31\"></use>","raindrop snowflake lightning cloud s31"],
  "lightsleetshowers_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s42d\"></use>","raindrop snowflake cloud s47 sun sun-glow-grad sun-inner-grad s42d"],
  "lightsleetshowers_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s42n\"></use>","raindrop snowflake cloud s47 moon moon-grad s42n"],
  "lightsnow": ["<use xlink:href=\"#s49\"></use>","snowflake cloud s49"],
  "lightsnowandthunder": ["<defs><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s33\"></use>","snowflake lightning cloud s33"],
  "lightsnowshowers_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s44d\"></use>","snowflake cloud s49 sun sun-glow-grad sun-inner-grad s44d"],
  "lightsnowshowers_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s44n\"></use>","snowflake cloud s49 moon moon-grad s44n"],
  "lightssleetshowersandthunder_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s26d\"></use>","raindrop snowflake lightning cloud s31 sun sun-glow-grad sun-inner-grad s26d"],
  "lightssleetshowersandthunder_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s26n\"></use>","raindrop snowflake lightning cloud s31 moon moon-grad s26n"],
  "lightssnowshowersandthunder_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s28d\"></use>","snowflake lightning cloud s33 sun sun-glow-grad sun-inner-grad s28d"],
  "lightssnowshowersandthunder_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s28n\"></use>","snowflake lightning cloud s33 moon moon-grad s28n"],
  "partlycloudy_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s03d\"></use>","cloud s04 sun sun-glow-grad sun-inner-grad s03d"],
  "partlycloudy_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s03n\"></use>","cloud s04 moon moon-grad s03n"],
  "rain": ["<use xlink:href=\"#s09\"></use>","raindrop cloud s09"],
  "rainandthunder": ["<defs><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s22\"></use>","raindrop lightning cloud s22"],
  "rainshowers_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s05d\"></use>","raindrop cloud s09 sun sun-glow-grad sun-inner-grad s05d"],
  "rainshowers_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s05n\"></use>","raindrop cloud s09 moon moon-grad s05n"],
  "rainshowersandthunder_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s06d\"></use>","raindrop lightning cloud s22 sun sun-glow-grad sun-inner-grad s06d"],
  "rainshowersandthunder_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s06n\"></use>","raindrop lightning cloud s22 moon moon-grad s06n"],
  "sleet": ["<use xlink:href=\"#s12\"></use>","raindrop snowflake cloud s12"],
  "sleetandthunder": ["<defs><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s23\"></use>","raindrop snowflake lightning cloud s23"],
  "sleetshowers_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s07d\"></use>","raindrop snowflake cloud s12 sun sun-glow-grad sun-inner-grad s07d"],
  "sleetshowers_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s07n\"></use>","raindrop snowflake cloud s12 moon moon-grad s07n"],
  "sleetshowersandthunder_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s20d\"></use>","raindrop snowflake lightning cloud s23 sun sun-glow-grad sun-inner-grad s20d"],
  "sleetshowersandthunder_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s20n\"></use>","raindrop snowflake lightning cloud s23 moon moon-grad s20n"],
  "snow": ["<use xlink:href=\"#s13\"></use>","snowflake cloud s13"],
  "snowandthunder": ["<defs><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s14\"></use>","snowflake lightning cloud s14"],
  "snowshowers_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s08d\"></use>","snowflake cloud s13 sun sun-glow-grad sun-inner-grad s08d"],
  "snowshowers_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s08n\"></use>","snowflake cloud s13 moon moon-grad s08n"],
  "snowshowersandthunder_day": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s21d\"></use>","snowflake lightning cloud s14 sun sun-glow-grad sun-inner-grad s21d"],
  "snowshowersandthunder_night": ["<defs><mask id=\"cloud_3_18_1_1_5\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#cloud\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"10\" transform=\"translate(3,18) scale(1,1)\"></use></mask><mask id=\"lightning_37_51_1_1_4\"><rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"white\"></rect><use xlink:href=\"#lightning\" fill=\"black\" stroke=\"black\" stroke-linejoin=\"round\" stroke-width=\"8\" transform=\"translate(37,51) scale(1,1)\"></use></mask></defs><use xlink:href=\"#s21n\"></use>","snowflake lightning cloud s14 moon moon-grad s21n"],
};
// </icons>

const SVG_OPEN = '<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">';

/** Code MET (avec ou sans suffixe jour/nuit) -> clé de ICONS ; repli sur « cloudy ». */
function iconKey(code) {
  const c = String(code ?? "").replace("_polartwilight", "_day");
  if (ICONS[c]) return c;
  if (ICONS[`${c}_day`]) return `${c}_day`;
  return "cloudy";
}

function iconUri(code) {
  const [body, ids] = ICONS[iconKey(code)];
  const defs = ids
    .split(" ")
    .map((id) => PARTS[id] ?? "")
    .join("");
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`${SVG_OPEN}${defs}${body}</svg>`)}`;
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** Données du flux, validées ; toute forme inattendue = flux inutilisable. */
function parse(data) {
  const now = data?.now;
  if (!isNum(now?.temp_c) || typeof now?.symbol !== "string") return null;
  const days = Array.isArray(data.days)
    ? data.days
        .filter((d) => typeof d?.date === "string" && isNum(d.min_c) && isNum(d.max_c) && typeof d.symbol === "string")
        .slice(0, 3)
    : [];
  return { now, days };
}

function css(node, text) {
  node.style.cssText = text;
  return node;
}

function box(tag, text, content) {
  const n = document.createElement(tag);
  css(n, text);
  if (content !== undefined) n.textContent = content;
  return n;
}

export class VdsContentWeather extends HTMLElement {
  #props = null;
  #ctx = null;
  #snap = null;

  configure(props, ctx) {
    const days = Number.isInteger(props?.days) ? Math.min(3, Math.max(1, props.days)) : 1;
    this.#props = {
      label: typeof props?.label === "string" ? props.label : "",
      days,
      imperial: props?.units === "imperial",
      maxAgeMs: (isNum(props?.max_age_s) && props.max_age_s > 0 ? props.max_age_s : 21600) * 1000,
      feedId: typeof props?.feed_id === "string" ? props.feed_id : "",
    };
    this.#ctx = ctx;
    this.style.cssText = "display:block;width:100%;height:100%;visibility:hidden;overflow:hidden";
  }

  async prepare() {
    const p = this.#props;
    const ctx = this.#ctx;
    if (!p || !ctx || !p.feedId || typeof ctx.feed !== "function") throw new Error("feed_unavailable");
    const snap = await ctx.feed(p.feedId);
    const data = snap ? parse(snap.data) : null;
    if (!snap || !data) throw new Error("feed_unavailable");
    const fetched = Date.parse(snap.fetchedAt);
    const nowMs = typeof ctx.now === "function" ? ctx.now() : Date.now();
    if (!Number.isFinite(fetched) || nowMs - fetched > p.maxAgeMs) throw new Error("feed_stale");
    this.#snap = data;
    this.#render(data);
  }

  #render(data) {
    const p = this.#props;
    const ctx = this.#ctx;
    const w = this.clientWidth || ctx.zone.width || 1280;
    const h = this.clientHeight || ctx.zone.height || 720;
    const mode = w / h >= BANNER_RATIO ? "banner" : h > w ? "portrait" : "landscape";
    this.dataset.layout = mode;
    const text = TEXT[ctx.locale] ?? TEXT.fr;
    const unit = mode === "banner" ? h : Math.min(w, h);
    const temp = (c) => `${Math.round(p.imperial ? (c * 9) / 5 + 32 : c)}°`;
    const dayName = (iso) => {
      const d = new Date(`${iso}T12:00:00Z`);
      try {
        return new Intl.DateTimeFormat(ctx.locale, { weekday: "short", timeZone: "UTC" }).format(d);
      } catch {
        return "";
      }
    };
    const img = (code, size) => {
      const i = document.createElement("img");
      i.alt = "";
      i.decoding = "async";
      i.src = iconUri(code);
      css(i, `width:${size}px;height:${size}px;flex:none;display:block`);
      return i;
    };
    const root = css(
      document.createElement("div"),
      `position:relative;box-sizing:border-box;width:100%;height:100%;display:flex;overflow:hidden;padding:${unit * 0.05}px;gap:${unit * 0.04}px;` +
        "background:var(--vds-surface, black);color:var(--vds-on-surface, white);font-family:var(--vds-font-body, sans-serif);" +
        `flex-direction:${mode === "landscape" || mode === "banner" ? "row" : "column"};align-items:center;justify-content:space-around`,
    );
    const label = (size) => box("div", `font-size:${size}px;font-weight:600;line-height:1.1;text-align:center;color:var(--vds-on-surface-variant, white);max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap`, p.label);
    const range = (d, size) => box("div", `font-size:${size}px;color:var(--vds-on-surface-variant, white);white-space:nowrap`, `${temp(d.min_c)} / ${temp(d.max_c)}`);
    const rows = p.days > 1 ? data.days.slice(0, p.days) : [];
    const today = data.days[0];

    const current = css(document.createElement("div"), `display:flex;align-items:center;gap:${unit * 0.04}px;flex-direction:${mode === "banner" ? "row" : "column"}`);
    if (mode !== "banner") current.append(label(unit * 0.075));
    current.append(img(data.now.symbol, unit * (mode === "banner" ? 0.8 : 0.42)));
    current.append(box("div", `font-size:${unit * (mode === "banner" ? 0.55 : 0.22)}px;font-weight:700;line-height:1`, temp(data.now.temp_c)));
    if (mode === "banner") {
      const side = css(document.createElement("div"), "display:flex;flex-direction:column;min-width:0");
      side.append(label(unit * 0.3));
      if (today) side.append(range(today, unit * 0.26));
      current.append(side);
    } else if (today && rows.length === 0) {
      current.append(range(today, unit * 0.08));
    }
    root.append(current);

    if (rows.length > 0) {
      const list = css(
        document.createElement("div"),
        mode === "portrait" || mode === "landscape"
          ? `display:flex;flex-direction:column;gap:${unit * 0.03}px`
          : `display:flex;flex-direction:row;gap:${unit * 0.12}px`,
      );
      const s = mode === "banner" ? unit * 0.5 : unit * 0.14;
      for (const d of rows) {
        const row = css(document.createElement("div"), `display:flex;align-items:center;gap:${unit * 0.03}px`);
        row.append(img(d.symbol, s));
        const col = css(document.createElement("div"), "display:flex;flex-direction:column");
        col.append(box("div", `font-size:${mode === "banner" ? unit * 0.26 : unit * 0.06}px;font-weight:600;text-transform:capitalize`, dayName(d.date)));
        col.append(range(d, mode === "banner" ? unit * 0.24 : unit * 0.06));
        row.append(col);
        list.append(row);
      }
      root.append(list);
    }

    // Mention obligatoire (CC BY 4.0), discrète.
    const attr = box(
      "div",
      mode === "banner"
        ? `position:absolute;right:${unit * 0.05}px;bottom:${unit * 0.03}px;font-size:${unit * 0.16}px;opacity:0.7;white-space:nowrap`
        : `position:absolute;right:${unit * 0.05}px;bottom:${unit * 0.025}px;font-size:${unit * 0.04}px;opacity:0.7;white-space:nowrap`,
      text.attribution,
    );
    attr.dataset.attribution = "";
    root.append(attr);
    this.replaceChildren(root);
  }

  start() {
    this.style.visibility = "visible";
  }

  stop() {
    this.style.visibility = "hidden";
    this.#snap = null;
    this.replaceChildren();
  }
}

if (!customElements.get("vds-content-weather")) {
  customElements.define("vds-content-weather", VdsContentWeather);
}
