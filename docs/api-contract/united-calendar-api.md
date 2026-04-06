# United Award Calendar API Contract

> Captured 2026-04-01 from united.com. Based on HAR capture of YYZ-LAX award search.
> Source: `docs/api-contract/har-captures/calendar-yyz-lax-economy.har`

---

## Table of Contents

1. [Endpoint Overview](#endpoint-overview)
2. [FetchAwardCalendar (Calendar Endpoint)](#fetchawardcalendar)
3. [FetchFlights (Detail Endpoint)](#fetchflights)
4. [Calendar vs Detail Relationship](#calendar-vs-detail-relationship)
5. [CabinType Value Mapping](#cabintype-value-mapping)
6. [BookingCode to Fare Class Mapping](#bookingcode-to-fare-class-mapping)
7. [Supporting Endpoints](#supporting-endpoints)
8. [Architecture Impact](#architecture-impact)

---

## Endpoint Overview

United's award search uses two primary data endpoints, plus two supporting endpoints:

| Endpoint | Purpose | Method | Response Size |
|---|---|---|---|
| `/api/flight/FetchAwardCalendar` | 30-day price calendar (all cabins) | POST | ~72 KB |
| `/api/flight/FetchFlights` | Per-date flight details | POST | ~595 KB |
| `/api/Flight/ShopValidate` | Search parameter validation | POST | ~3 KB |
| `/api/flight/GetFareColumns` | Fare column display definitions | POST | ~376 B |

Base URL: `https://www.united.com`

---

## FetchAwardCalendar

Returns a 30-day calendar of lowest award prices per day, covering **all cabin classes** in a single response. This is the primary endpoint for bulk data collection.

### URL

```
POST https://www.united.com/api/flight/FetchAwardCalendar
```

### Request Headers

| Header | Value | Required |
|---|---|---|
| `Content-Type` | `application/json` | Yes |
| `x-authorization-api` | `bearer {token}` | Yes |
| `User-Agent` | Standard Chrome user-agent string | Yes |
| `Accept` | `application/json` | Yes |
| `sec-ch-ua` | Chrome version hint | Recommended |
| `sec-ch-ua-platform` | `"Windows"` | Recommended |
| `sec-fetch-dest` | `empty` | Recommended |
| `sec-fetch-mode` | `cors` | Recommended |
| `sec-fetch-site` | `same-origin` | Recommended |
| `Origin` | `https://www.united.com` | Recommended |
| `Referer` | `https://www.united.com/en/us/fsr/...` | Recommended |

The `x-authorization-api` header carries the bearer token. See `united-auth-flow.md` for how to obtain it. Standard `sec-*` headers from a Chrome browser should be included to avoid Cloudflare fingerprint mismatches.

### Request Body

```json
{
  "SearchTypeSelection": 1,
  "SortType": "bestmatches",
  "SortTypeDescending": false,
  "Trips": [
    {
      "Origin": "YYZ",
      "Destination": "LAX",
      "DepartDate": "2026-04-02",
      "Index": 1,
      "TripIndex": 1,
      "SearchRadiusMilesOrigin": 0,
      "SearchRadiusMilesDestination": 0,
      "DepartTimeApprox": 0,
      "SearchFiltersIn": {
        "FareFamily": "ECONOMY",
        "AirportsStop": null,
        "AirportsStopToAvoid": null,
        "ShopIndicators": {
          "IsTravelCreditsApplied": false,
          "IsDoveFlow": true
        }
      }
    }
  ],
  "CabinPreferenceMain": "economy",
  "CartId": "{GUID}",
  "PaxInfoList": [{"PaxType": 1}],
  "AwardTravel": true,
  "NGRP": true,
  "CalendarLengthOfStay": -1,
  "PetCount": 0,
  "RecentSearchKey": "YYZLAX4/2/2026",
  "CalendarFilters": {
    "Filters": {
      "PriceScheduleOptions": {"Stops": 1}
    }
  },
  "Characteristics": [
    {"Code": "SOFT_LOGGED_IN", "Value": false},
    {"Code": "UsePassedCartId", "Value": false}
  ],
  "FareType": "mixedtoggle",
  "BuildHashValue": "true",
  "BBXSolutionSetIdSelected": null,
  "FlexibleDaysAfter": 0,
  "FlexibleDaysBefore": 0
}
```

### Request Body Field Reference

| Field | Type | Required | Description |
|---|---|---|---|
| `SearchTypeSelection` | int | Yes | Search type. `1` = one-way award search. |
| `SortType` | string | Yes | Sort order for results. `"bestmatches"` is the default. |
| `SortTypeDescending` | bool | Yes | Sort direction. `false` = ascending. |
| `Trips` | array | Yes | Array of trip legs. One element for one-way, two for round-trip. |
| `Trips[].Origin` | string | Yes | 3-letter IATA origin airport code (e.g., `"YYZ"`). |
| `Trips[].Destination` | string | Yes | 3-letter IATA destination airport code (e.g., `"LAX"`). |
| `Trips[].DepartDate` | string | Yes | Departure date in `YYYY-MM-DD` format. This anchors the calendar window. |
| `Trips[].Index` | int | Yes | Leg index. Always `1` for one-way. |
| `Trips[].TripIndex` | int | Yes | Trip index. Always `1` for one-way. |
| `Trips[].SearchRadiusMilesOrigin` | int | No | Search radius around origin. `0` = exact airport only. |
| `Trips[].SearchRadiusMilesDestination` | int | No | Search radius around destination. `0` = exact airport only. |
| `Trips[].DepartTimeApprox` | int | No | Approximate departure time filter. `0` = any time. |
| `Trips[].SearchFiltersIn.FareFamily` | string | Yes | Fare family filter. `"ECONOMY"` is the default. **Does not restrict cabin classes in the response** -- all cabins are always returned. |
| `Trips[].SearchFiltersIn.ShopIndicators.IsDoveFlow` | bool | No | Internal UI flow indicator. Set to `true`. |
| `CabinPreferenceMain` | string | Yes | Cabin preference hint. `"economy"` is default. **Does not restrict response to economy only** -- all cabins are returned regardless of this value. |
| `CartId` | string | Yes | A GUID identifying the search session (e.g., `"a1b2c3d4-e5f6-7890-abcd-ef1234567890"`). Can be generated client-side. |
| `PaxInfoList` | array | Yes | Passenger list. `[{"PaxType": 1}]` = 1 adult. |
| `PaxInfoList[].PaxType` | int | Yes | Passenger type. `1` = adult. |
| `AwardTravel` | bool | Yes | **Must be `true`** for award/miles pricing. `false` returns cash fares. |
| `NGRP` | bool | Yes | Enable Next Generation Revenue Pricing. Set to `true`. |
| `CalendarLengthOfStay` | int | Yes | **`-1` for calendar view.** `0` triggers the FetchFlights detail view instead. |
| `PetCount` | int | No | Number of pets. `0` for standard searches. |
| `RecentSearchKey` | string | No | Cache key for recent searches. Format: `"{ORIGIN}{DEST}{M/D/YYYY}"`. |
| `CalendarFilters.Filters.PriceScheduleOptions.Stops` | int | No | Maximum stops filter. `1` = 1 stop max. |
| `Characteristics` | array | No | Key-value pairs for session behavior. |
| `FareType` | string | Yes | Fare type toggle. `"mixedtoggle"` returns both Saver and Standard fares. |
| `BuildHashValue` | string | No | `"true"` to include hash values for result caching. |
| `BBXSolutionSetIdSelected` | string | No | For paginating within a previous result set. `null` for initial search. |
| `FlexibleDaysAfter` | int | No | Flexible date range after departure. `0` = exact date. |
| `FlexibleDaysBefore` | int | No | Flexible date range before departure. `0` = exact date. |

### Response

**Status**: `200 OK`
**Content-Type**: `application/json`
**Size**: ~72 KB (for 30 days, YYZ-LAX)

### Response Schema

```
{
  "data": {
    "CalendarLengthOfStay": int,          // -1 for calendar view
    "CartId": string,                      // Echo of request CartId
    "Characteristics": [{                  // Server metadata
      "Code": string,
      "Value": string
    }],
    "Timings": [{                          // Server performance metrics
      "Name": string,
      "TimeMilliseconds": string
    }],
    "LangCode": string,                   // "en-US"
    "LastCallDateTime": string,            // "2026-04-01 09:18 AM"
    "LastTripIndexRequested": int,         // 1
    "ServerName": string,                  // Internal server ID (redact)
    "Status": int,                         // 1 = success
    "TripCount": int,                      // Number of trip legs
    "Calendar": {
      "LoadedFromCache": bool,             // Whether result came from server cache
      "LengthOfStay": int,                // 6 (internal)
      "MaxLengthOfStay": int,             // 6 (internal)
      "AdvancePurchase": int,             // 14 (minimum advance purchase days)
      "CalendarWindow": int,              // 30 (days covered by this response)
      "Months": [{
        "ShowPreviousMonthIndicator": bool,
        "ShowNextMonthIndicator": bool,
        "Year": int,                       // e.g., 2026
        "Month": int,                      // e.g., 4 (April)
        "Weeks": [{
          "Year": int,
          "Month": int,
          "Days": [<Day>]                  // See Day schema below
        }]
      }]
    },
    "Trips": [{                            // Trip metadata (mirrors request)
      "DepartDate": string,
      "Index": int,
      "TripIndex": int,
      "SearchFiltersIn": {...},
      "SearchFiltersOut": {
        "CabinCountMin": int,
        "CabinCountMax": int              // Max cabins per day (e.g., 3)
      },
      "Flights": []                        // Always empty in calendar response
    }]
  }
}
```

### Day Schema

Each day in `Months[].Weeks[].Days[]`:

```
{
  "Year": int,                             // e.g., 2026
  "Month": int,                            // e.g., 4
  "Cheapest": bool,                        // Whether this is the cheapest day
  "CabinCount": int,                       // Number of distinct cabins available (e.g., 3)
  "DateValue": string,                     // Date in MM/DD/YYYY format (e.g., "04/01/2026")
  "DayNotInThisMonth": bool,               // true for padding days outside the month
  "Display": bool,                         // UI display flag
  "DisplayFare": float,                    // 0.0 (unused in award mode)
  "Economy": bool,                         // Economy availability flag
  "First": bool,                           // First class availability flag
  "LastResultId": string,                  // Opaque ID for result caching / pagination
  "TravelDateRange": bool,                 // Date range indicator
  "DayFareInfos": [],                      // Always empty in observed responses
  "Solutions": [<Solution>]                // See Solution schema below
}
```

**Padding days**: Days at the beginning/end of a month grid where `DayNotInThisMonth: true` have empty `Solutions[]` arrays. These are calendar layout padding and should be skipped.

### Solution Schema

Each solution in `Days[].Solutions[]`:

```
{
  "AwardType": string,                    // "Saver" or "Standard"
  "CabinType": string,                    // See CabinType mapping table below
  "Prices": [
    {
      "Currency": "MILES",                // Miles cost
      "Amount": float,                    // e.g., 22500.0
      "AmountAllPax": float,              // 0.0 (for single pax)
      "AmountBase": float,                // 0.0
      "AmountDiffPerPax": float,          // 0.0
      "PricingType": "Award"              // Price category
    },
    {
      "Currency": "USD",                  // Taxes/fees in USD
      "Amount": float,                    // e.g., 68.51
      "AmountAllPax": float,              // 0.0
      "AmountBase": float,                // 0.0
      "AmountDiffPerPax": float,          // 0.0
      "PricingType": "Tax"                // Price category
    }
  ],
  "Cheapest": bool,                       // Whether this is the cheapest solution for the day
  "DateValue": string,                    // Date (echoed from parent Day)
  "DayNotInThisMonth": bool,              // Echoed from parent
  "Display": bool,                        // UI flag
  "DisplayFare": float,                   // 0.0
  "TravelDateRange": bool,                // Range indicator
  "DayFareInfos": []                      // Always empty in observed responses
}
```

### What the Calendar Returns (and Does Not Return)

**Included in calendar response:**
- Lowest award price per cabin class per day (miles + taxes)
- Both Saver and Standard award types
- All cabin classes (economy, premium economy, business, first) in a single response
- 30 days of data per request
- `CabinCount` per day indicating number of distinct cabin tiers available

**NOT included in calendar response:**
- No flight numbers
- No departure/arrival times
- No connection information (nonstop vs connecting)
- No seat availability counts
- No booking codes / fare class codes
- No operating carrier information
- No aircraft type information

### Empirical Data (YYZ-LAX, April 2026)

From the captured sample response (`sample-responses/calendar-response.json`):

- **Days with data**: 30 out of 30 (04/01 through 04/30)
- **Solutions per day**: 4-6 (varies by availability)
- **First class available**: 11 of 30 days
- **CabinCount**: consistently 3 across all days

**Price ranges observed (miles)**:

| Cabin/Award Type | Min Miles | Max Miles | Days Present |
|---|---|---|---|
| Saver Economy | 12,900 | 22,500 | 26/30 |
| Standard Economy | 16,500 | 40,000 | 4/30 |
| Saver Business (mixed OK) | 30,000 | 65,000 | 18/30 |
| Saver Business (not mixed) | 30,000 | 65,000 | 5/30 |
| Standard Business (mixed OK) | 57,900 | 77,900 | 12/30 |
| Standard Business (not mixed) | 57,900 | 95,700 | 25/30 |
| Saver First (mixed OK) | 30,000 | 75,000 | 11/30 |
| Saver First (not mixed) | 30,000 | 30,000 | 1/30 |
| Standard Premium Economy | 100,000 | 100,000 | 30/30 |

**Taxes**: $68.51 USD consistent across all days and cabins for this route (YYZ-LAX international).

---

## FetchFlights

Returns per-flight detail for a single date, including flight numbers, times, connections, products, booking codes, and seat availability. This is the detail endpoint triggered when a user clicks a specific date in the calendar.

### URL

```
POST https://www.united.com/api/flight/FetchFlights
```

### Request Headers

Same as FetchAwardCalendar. See [Request Headers](#request-headers) above.

### Request Body

The request body is **identical** to FetchAwardCalendar with one critical difference:

```
"CalendarLengthOfStay": 0    // 0 = detail view (not -1)
```

All other fields remain the same. The `DepartDate` in `Trips[]` specifies the single date to fetch flights for.

### Response

**Status**: `200 OK`
**Content-Type**: `application/json`
**Size**: ~595 KB (for a single date, YYZ-LAX)

### Response Schema (Top Level)

```
{
  "data": {
    "AnonymousSearch": bool,               // Whether the user is logged in
    "ArrivalAirports": string,             // "LAX"
    "CalendarLengthOfStay": int,           // -1 (echoed, confusingly)
    "CallTimeBBX": string,                 // Backend query time in ms
    "CartId": string,
    "CountryCode": string,                 // "US"
    "DepartureAirports": string,           // "YYZ"
    "EquipmentTypes": string,              // Comma-separated aircraft codes
    "LangCode": string,                    // "en-US"
    "LastBBXSolutionSetId": string,        // Pagination token
    "MarketingCarriers": string,           // "UA,AC"
    "MidPoints": string,                   // Connection airports: "DCA,DEN,EWR,IAD,IAH,LGA,ORD,PIT,SFO"
    "OperatingCarriers": string,           // "UA,AC"
    "PageCount": int,                      // Total pages
    "PageCurrent": int,                    // Current page
    "Status": int,                         // 1 = success
    "Calendar": {
      "CalendarWindow": int,               // 61 in detail mode
      "Months": []                         // Empty in detail mode
    },
    "Errors": [],
    "Warnings": [{                         // Non-fatal warnings
      "MajorCode": string,
      "MajorDescription": string,
      "MinorCode": string,
      "MinorDescription": string,
      "Message": string
    }],
    "Trips": [{
      "Destination": string,
      "DestinationDecoded": string,        // "Los Angeles, CA, US (LAX)"
      "Origin": string,
      "OriginDecoded": string,             // "Toronto, ON, CA (YYZ)"
      "DepartDate": string,                // "2026-04-02"
      "ColumnInformation": {<Columns>},    // Fare column definitions
      "SearchFiltersIn": {...},
      "SearchFiltersOut": {
        "AirportsStop": string,            // "DCA,DEN,EWR,IAD,IAH,LGA,ORD,PIT,SFO"
        "CabinCountMin": int,
        "CabinCountMax": int,
        "DurationMin": int,                // Shortest flight in minutes
        "DurationMax": int,                // Longest flight in minutes
        "StopCountMin": int,               // Min stops (1 = all connecting)
        "StopCountMax": int,               // Max stops
        "PriceMin": float,                 // Lowest price in miles
        "PriceMax": float                  // Highest price in miles
      },
      "FareFamilyFilters": [{...}],
      "Flights": [<Flight>],              // See Flight schema below
      "FlightCount": int                   // Total flights returned
    }]
  }
}
```

### Flight Schema

Each flight in `Trips[].Flights[]`:

```
{
  "DepartDateTime": string,                // "2026-04-02 10:45"
  "BBXHash": string,                       // Opaque hash for caching
  "BBXSolutionSetId": string,              // Solution set for pagination
  "BookingClassAvailability": string,      // "J1|JN1|C1|D1|Z1|...|XN0"
  "CabinCount": int,                       // Cabin tiers on this flight
  "Destination": string,                   // First segment destination: "DEN"
  "DestinationCountryCode": string,        // "US"
  "DestinationDateTime": string,           // "2026-04-02 12:40"
  "DestinationDescription": string,        // "Denver, CO, US (DEN)"
  "FlightNumber": string,                  // "1944"
  "International": bool,                   // true for cross-border
  "MarketingCarrier": string,              // "UA"
  "MarketingCarrierDescription": string,   // "United Airlines"
  "MileageActual": int,                    // Distance in miles
  "OperatingCarrier": string,              // "UA"
  "OperatingCarrierDescription": string,   // "United Airlines"
  "Origin": string,                        // "YYZ"
  "OriginDescription": string,             // "Toronto, ON, CA (YYZ)"
  "TravelMinutesTotal": int,              // Total travel time including connections
  "TravelMinutes": int,                   // This segment flight time only
  "Connections": [<Connection>],           // See Connection schema
  "Products": [<Product>],                // See Product schema
  "StopInfos": [],                         // Intermediate stops (non-connection)
  "EquipmentDisclosures": {
    "EquipmentType": string,               // "739"
    "EquipmentDescription": string,        // "Boeing 737-900ER"
    "IsSingleCabin": bool
  },
  "Warnings": [{
    "Title": string,                       // "Change of Terminal"
    "Key": string,                         // "CHANGE_OF_TERMINAL"
    "Messages": [string]
  }]
}
```

### Connection Schema

Each connection in `Flights[].Connections[]`:

```
{
  "DepartDateTime": string,                // "2026-04-02 13:25"
  "BookingClassAvailability": string,      // Per-segment availability
  "ConnectTimeMinutes": int,               // Layover duration: 45
  "Destination": string,                   // "LAX"
  "DestinationDateTime": string,           // "2026-04-02 14:59"
  "DestinationDescription": string,
  "FlightNumber": string,                  // "774"
  "IsConnection": bool,                    // true
  "MarketingCarrier": string,              // "UA"
  "OperatingCarrier": string,              // "UA"
  "Origin": string,                        // "DEN"
  "TravelMinutes": int,                   // Segment flight time
  "ParentFlightNumber": string,            // "1944" (links to parent)
  "Products": [<Product>],                // Segment-level products
  "EquipmentDisclosures": {
    "EquipmentType": string,
    "EquipmentDescription": string
  },
  "Connections": []                        // Can nest for 2+ stop itineraries
}
```

### Product Schema

Each product in `Flights[].Products[]` (also appears in Connections):

```
{
  "BookingCode": string,                   // "YN", "JN", "X", "I", etc.
  "BookingClassAvailability": string,      // Full BCA string for this segment
  "CabinType": string,                    // "Coach" or "First"
  "CabinTypeText": string,                // "(lowest)" or ""
  "CabinTypeCode": string,                // "UE" (United Economy), "UF" (United First)
  "Description": string,                   // "United Economy", "United First"
  "Mileage": int,                          // Segment distance
  "ProductPath": string,                   // "Award" (miles) or "Reward" (placeholder)
  "ProductSubtype": string,                // "DISPLACEMENT" or "NonExistingProductPlaceHolder"
  "ProductType": string,                   // CabinType code (e.g., "MIN-ECONOMY-SURP-OR-DISP")
  "AwardType": string,                    // "Saver" or "Standard"
  "FareFamily": string,                   // Same as ProductType
  "FareFlavour": string,                  // "displacement" (dynamic pricing)
  "IsDynamicallyPriced": int,             // 0 = fixed, 1 = dynamic
  "Fares": [{
    "FareBasisCode": string               // e.g., "XC03VO"
  }],
  "Context": {                             // Pricing context (on parent flight Products only)
    "ItaMiles": string,                    // Original ITA miles: "60000"
    "NgrpMiles": string,                   // NGRP (discounted) miles: "40000"
    "NGRP": bool,                          // true if NGRP pricing applied
    "DynamicCode": string,                 // e.g., "E40K-U-Y"
    "PaxPrices": [{
      "PaxType": string,                  // "ADT"
      "Miles": float,                     // 40000.0
      "Amount": float                     // 0.0 (cash component)
    }]
  },
  "Prices": [                              // Present on parent Products, empty on Connection Products
    {
      "Currency": "MILES",
      "Amount": float,                    // e.g., 40000.0
      "PricingType": "Award"
    },
    {
      "Currency": "USD",
      "Amount": float,                    // e.g., 68.51
      "PricingType": "Tax"
    }
  ]
}
```

**Note on Products with `ProductSubtype: "NonExistingProductPlaceHolder"`**: These represent cabin classes that are not available on this specific flight. They have empty `BookingCode` and empty `Prices[]`. Skip them during parsing.

### BookingClassAvailability (BCA) String

The `BookingClassAvailability` field encodes seat counts per booking class:

```
"J1|JN1|C1|D1|Z1|ZN1|P1|PN0|PZ0|IN0|Y5|YN5|B5|M4|E4|U4|H4|HN4|Q3|V3|W3|S3|T2|L2|K2|G1|N5|XN0"
```

Format: `{ClassCode}{SeatCount}` separated by `|`

| Class | Cabin | Type | Seats in Example |
|---|---|---|---|
| J | Business/First | Revenue premium | 1 |
| JN | Business/First | Standard award premium | 1 |
| C | Business/First | Revenue premium | 1 |
| D | Business/First | Revenue premium | 1 |
| Z | Business/First | Revenue premium | 1 |
| ZN | Business/First | Standard award premium | 1 |
| P | Business/First | Revenue premium | 1 |
| PN | Business/First | Premium award | 0 |
| PZ | Business/First | Premium award | 0 |
| IN | Business/First | Saver award premium | 0 |
| Y | Economy | Revenue full fare | 5 |
| YN | Economy | Standard award economy | 5 |
| B | Economy | Revenue discount | 5 |
| M-U | Economy | Revenue discount tiers | 2-4 |
| HN | Economy | Award economy tier | 4 |
| Q-S | Economy | Revenue deep discount | 0-3 |
| T-K | Economy | Revenue deep discount | 2 |
| G | Economy | Revenue lowest | 1 |
| N | Economy | Revenue | 5 |
| XN | Economy | Saver award economy | 0 |

**Key insight**: `XN0` means zero saver economy seats on this specific flight. `IN0` means zero saver business seats. The seat count is per booking class, not per cabin. A `0` count means that fare class is sold out.

### Empirical Data (YYZ-LAX, April 2, 2026)

From the captured sample response (`sample-responses/detail-response.json`):

- **Flights returned**: 46 for the full response (sample file truncated to 3)
- **All flights were connecting** (no nonstop YYZ-LAX on United)
- **Connection airports**: DCA, DEN, EWR, IAD, IAH, LGA, ORD, PIT, SFO
- **Marketing carriers**: UA, AC (Air Canada codeshare)
- **Operating carriers**: UA, AC
- **Stop range**: 1-2 stops
- **Duration range**: 432-872 minutes (7h12m to 14h32m)
- **Price range**: 40,000-200,000 miles

---

## Calendar vs Detail Relationship

The two endpoints serve fundamentally different purposes and return non-overlapping data:

```
FetchAwardCalendar                    FetchFlights
(CalendarLengthOfStay: -1)            (CalendarLengthOfStay: 0)
         |                                     |
   30 days of data                      1 day of data
   Price summaries only                 Full flight details
   All cabins included                  All cabins included
   ~72 KB response                      ~595 KB response
   NO flight numbers                    Flight numbers
   NO times                             Departure/arrival times
   NO connections                       Connection details
   NO seat counts                       BookingClassAvailability
   NO booking codes                     BookingCode per product
   NO carrier info                      Operating/marketing carrier
```

### Request Differentiation

The **only** field that differs between the two requests is:

| Field | Calendar | Detail |
|---|---|---|
| `CalendarLengthOfStay` | `-1` | `0` |

Everything else -- origin, destination, date, cabin preference, passengers -- is identical.

### When to Use Each

| Use Case | Endpoint | Reason |
|---|---|---|
| Daily price sweep (bulk) | FetchAwardCalendar | 1 request = 30 days, all cabins |
| Saver availability detection | FetchAwardCalendar | AwardType field distinguishes Saver vs Standard |
| Flight schedule lookup | FetchFlights | Only source of flight numbers and times |
| Seat count verification | FetchFlights | BookingClassAvailability has per-class counts |
| Nonstop filtering | FetchFlights | Only source of connection count |
| Booking code extraction | FetchFlights | BookingCode (X, I, YN, JN) only here |

---

## CabinType Value Mapping

The `CabinType` field in calendar Solutions uses internal codes that map to user-facing cabin classes:

| CabinType Code | Cabin Class | Description |
|---|---|---|
| `MIN-ECONOMY-SURP-OR-DISP` | Economy | Lowest economy award (surplus or displacement pricing) |
| `ECO-PREMIUM-DISP` | Premium Economy | Premium economy displacement pricing |
| `MIN-BUSINESS-SURP-OR-DISP` | Business/First (lowest) | Lowest business/first award, may include mixed-cabin itineraries |
| `MIN-BUSINESS-SURP-OR-DISP-NOT-MIXED` | Business/First (not mixed) | Business/first award where all segments are in the same cabin |
| `MIN-FIRST-SURP-OR-DISP` | First | Lowest first class award, may include mixed-cabin itineraries |
| `MIN-FIRST-SURP-OR-DISP-NOT-MIXED` | First (not mixed) | First class award where all segments are first class |

### Understanding "Mixed" vs "Not Mixed"

For connecting itineraries, United distinguishes between:

- **Mixed cabin**: e.g., Economy YYZ-ORD then First ORD-LAX. The "Business/First (lowest)" column includes these mixed results, showing the cheapest way to get *any* premium cabin segment.
- **Not mixed**: All segments are in the same cabin class. This is the "true" business or first class experience end-to-end.

The `MIN-BUSINESS-SURP-OR-DISP` code maps to the UI column labeled "Business/First (lowest)" with `Description: "(lowest)"`. The `MIN-BUSINESS-SURP-OR-DISP-NOT-MIXED` code maps to the same header but with `FareContentDescription: "Not mixed"`.

### Understanding "SURP-OR-DISP"

This refers to United's pricing models:
- **SURP** (Surplus): Traditional fixed award chart pricing. This is "saver" pricing.
- **DISP** (Displacement): Dynamic pricing based on the revenue value of the seat. This is "standard" pricing.

The "SURP-OR-DISP" suffix means the field shows whichever is cheaper between surplus and displacement for that cabin.

### Mapping Solutions to Simplified Cabin Names

For the scraper's database, map as follows:

| CabinType Code | Simplified Cabin | Notes |
|---|---|---|
| `MIN-ECONOMY-SURP-OR-DISP` | `economy` | |
| `ECO-PREMIUM-DISP` | `premium_economy` | |
| `MIN-BUSINESS-SURP-OR-DISP` | `business` | Lowest biz/first, may be mixed cabin |
| `MIN-BUSINESS-SURP-OR-DISP-NOT-MIXED` | `business_pure` | All segments in business/first |
| `MIN-FIRST-SURP-OR-DISP` | `first` | May be mixed cabin |
| `MIN-FIRST-SURP-OR-DISP-NOT-MIXED` | `first_pure` | All segments in first class |

---

## BookingCode to Fare Class Mapping

From the FetchFlights response, the `BookingCode` field on Products identifies the fare class:

| BookingCode | Cabin | Award Type | Description |
|---|---|---|---|
| `X` | Economy | Saver | Saver economy award (lowest miles, partner-bookable) |
| `XN` | Economy | Saver | Same as X (alternate notation in BCA) |
| `YN` | Economy | Standard | Standard (dynamic) economy award |
| `I` | Business/First | Saver | Saver premium award (lowest miles, partner-bookable) |
| `IN` | Business/First | Saver | Same as I (alternate notation in BCA) |
| `JN` | Business/First | Standard | Standard (dynamic) premium award |

### Why This Matters

- **Saver awards** (X, I) are bookable through partner programs (e.g., Aeroplan, ANA). These are the highest-value finds.
- **Standard awards** (YN, JN) are only bookable through United's own program and use dynamic pricing.
- The calendar endpoint's `AwardType: "Saver"` vs `"Standard"` field maps directly to these booking codes, so **the calendar provides saver detection without needing the detail endpoint**.

---

## Supporting Endpoints

### ShopValidate

```
POST https://www.united.com/api/Flight/ShopValidate
```

Validates search parameters before the main search. Returns a ~3 KB response. Called by the UI before FetchAwardCalendar. Not strictly required for scraping -- invalid parameters to FetchAwardCalendar will simply return empty results or error responses.

### GetFareColumns

```
POST https://www.united.com/api/flight/GetFareColumns
```

Returns column definitions for the fare display matrix. Response is ~376 bytes. Maps CabinType codes to display labels. Not needed for scraping -- the mapping is static and documented in [CabinType Value Mapping](#cabintype-value-mapping) above.

### Airport Lookup

```
GET https://www.united.com/api/airports/lookup/?airport={code}&allAirports=true&matches=0
```

Returns airport information for a given IATA code. Potentially useful for validating route inputs, but not needed for core scraping.

---

## Architecture Impact

This section answers the seven critical architecture questions from the project brief, with cited evidence from the HAR capture data.

### 1. Calendar Data Scope: Does one request return all cabins?

**Answer: YES -- all cabin classes are returned in a single response, regardless of the `CabinPreferenceMain` value.**

**Evidence**: The captured request set `CabinPreferenceMain: "economy"` and `SearchFiltersIn.FareFamily: "ECONOMY"`, yet the response contains Solutions with CabinType values spanning economy, premium economy, business, and first class:

- `MIN-ECONOMY-SURP-OR-DISP` (economy)
- `ECO-PREMIUM-DISP` (premium economy)
- `MIN-BUSINESS-SURP-OR-DISP` (business/first lowest)
- `MIN-BUSINESS-SURP-OR-DISP-NOT-MIXED` (business/first not mixed)
- `MIN-FIRST-SURP-OR-DISP` (first)
- `MIN-FIRST-SURP-OR-DISP-NOT-MIXED` (first not mixed)

Source: `sample-responses/calendar-response.json`, any Day with Solutions (e.g., 04/04/2026 has 6 Solutions covering all cabin types).

**Impact**: No need to multiply requests by cabin count. One request per route per 30-day window covers all cabins. This is the best possible outcome for scrape volume.

### 2. Flight Detail Availability: Calendar vs Detail

**Answer: The calendar endpoint returns ONLY daily price summaries. Flight numbers, departure/arrival times, connections, seat counts, and booking codes are NOT available in the calendar response. A separate FetchFlights request is required for flight-level detail.**

**Evidence**: Compare the calendar Day object (which has `Solutions[]` containing only `AwardType`, `CabinType`, and `Prices[]`) against the FetchFlights response (which has `Flights[]` containing `FlightNumber`, `DepartDateTime`, `Connections[]`, `BookingClassAvailability`, `Products[]` with `BookingCode`).

The calendar response's `Trips[].Flights` array is explicitly empty (`"Flights": []`).

Source: `sample-responses/calendar-response.json` line 4600 shows `"Flights": []`. `sample-responses/detail-response.json` line 1224+ shows full flight data.

**Impact**: If flight-level detail is required (seat counts, nonstop filtering, booking codes), a second request per date is needed. This multiplies scrape volume by up to 337x per route (one per date). See question 7 for volume implications.

### 3. Days Per Response

**Answer: Exactly 30 days per calendar request.**

**Evidence**: `Calendar.CalendarWindow: 30` in the response. The captured response for a search anchored to 2026-04-02 returns data for 04/01/2026 through 04/30/2026 (the full calendar month). Days outside the month (03/29, 03/30, 03/31) appear as padding with `DayNotInThisMonth: true` and empty Solutions.

Source: `sample-responses/calendar-response.json`, `Calendar.CalendarWindow` field and 30 days with non-empty Solutions.

**Impact**: Covering 337 days requires `ceil(337 / 30) = 12` requests per route, confirming the project brief's estimate. The date anchoring determines which 30-day window is returned. To cover a full year, advance the `DepartDate` by 30 days per request (e.g., Apr 2, May 2, Jun 1, ...).

### 4. Seat Count Availability

**Answer: Seat counts are NOT available in the calendar response. They are only available in the FetchFlights response via the `BookingClassAvailability` field.**

**Evidence**: Calendar Day and Solution objects contain no seat count fields. The FetchFlights Flight object contains `BookingClassAvailability: "J1|JN1|C1|D1|Z1|ZN1|P1|PN0|PZ0|IN0|Y5|YN5|..."` where the number after each class code indicates available seats.

Source: `sample-responses/detail-response.json`, line 1228.

**Impact**: If alerts need to filter on "minimum N seats available," the detail endpoint is required. However, for basic "is saver availability present?" detection, the calendar's `AwardType: "Saver"` field is sufficient without seat counts.

### 5. Fare Class Codes

**Answer: Fare class codes (BookingCode) are only available in the FetchFlights response, not in the calendar. However, the calendar's `AwardType` field ("Saver" vs "Standard") provides equivalent saver detection.**

**Evidence**: FetchFlights Products contain `BookingCode` values:
- `X` / `XN` = saver economy
- `I` / `IN` = saver business/first
- `YN` = standard economy
- `JN` = standard business/first

The calendar's Solutions contain `AwardType: "Saver"` or `"Standard"` which maps directly to these booking code families.

Source: `sample-responses/detail-response.json`, Products at lines 1297, 1347, 1376, 1434, 1552.

**Impact**: For saver detection (the primary use case), the calendar alone is sufficient. The `AwardType` field directly indicates whether saver inventory exists on a given date. BookingCode specifics (X vs I) are only needed if the scraper must distinguish fare classes for partner program booking guidance, which requires the detail endpoint.

### 6. Direct vs Connecting Flights

**Answer: The calendar response does NOT indicate whether flights are nonstop or connecting. This information is only available in FetchFlights via the `Connections[]` array.**

**Evidence**: Calendar Solutions contain no fields related to stops, connections, or routing. The FetchFlights response includes `Connections[]` arrays on each Flight object (with `ConnectTimeMinutes`, connection airport, and connecting flight details), and `SearchFiltersOut.StopCountMin` / `StopCountMax` at the trip level.

For the YYZ-LAX sample, `StopCountMin: 1` and `StopCountMax: 2` indicate all flights had at least 1 stop (no nonstops available on this route).

Source: `sample-responses/detail-response.json`, lines 1259-1430 (Connections), lines 479-480 (StopCountMin/Max).

**Impact**: The `direct` boolean in the database schema cannot be populated from calendar data alone. If nonstop filtering is a feature requirement, the detail endpoint is needed at least once per route to establish whether nonstop service exists (this is relatively static -- airline schedules don't change daily). Alternatively, maintain a separate nonstop route lookup table built from a one-time detail scan.

### 7. Revised Scrape Volume Estimate

Based on empirical findings, here are the updated volume calculations:

#### Calendar-Only Approach (Recommended)

This approach collects daily prices across all cabins with saver/standard distinction. Sufficient for the core use case: "alert me when saver business class is available on route X for under Y miles."

```
20,000 routes x 12 requests/route (337 days / 30 days per request) = 240,000 requests/day
```

This confirms the project brief's estimate of 240,000 requests/day for a full sweep.

At ~72 KB per response: **240,000 x 72 KB = ~17.3 GB/day of raw response data.**

#### Calendar + Detail Approach (If Flight Details Needed)

If per-flight data is required for every date on every route:

```
20,000 routes x 337 dates = 6,740,000 requests/day
```

At ~595 KB per response: **6,740,000 x 595 KB = ~4.0 TB/day of raw response data.**

**This is infeasible.** At 5 requests/second, it would take 15.6 days to complete a single sweep -- it can never catch up.

#### Hybrid Approach (Practical Compromise)

Use the calendar for bulk sweeps. Only fetch flight details on-demand for:
1. Routes/dates where saver availability is detected in the calendar
2. Routes where users have active alerts that require seat count or nonstop filtering
3. A rotating subset of routes to build/maintain a nonstop route lookup table

Estimated hybrid volume:

| Component | Calculation | Requests/Day |
|---|---|---|
| Full calendar sweep | 20,000 routes x 12 windows | 240,000 |
| Saver detail lookup | ~5% of route-dates have saver (~33,700 route-dates) | ~33,700 |
| Alert route details | 500 routes x 12 cycles x ~10 dates with saver | ~60,000 |
| Nonstop route scan | 1,000 routes/day x 1 date each | 1,000 |
| **Total** | | **~334,700** |

This is roughly 35% more than the calendar-only approach but provides flight-level detail where it matters most.

#### Recommendation

**Start with calendar-only.** The calendar endpoint provides sufficient data for the MVP use case (saver availability monitoring with price alerts). Defer flight detail collection to Phase 2 when the calendar scraping pipeline is proven stable.

The calendar provides:
- All cabin prices per day (saver and standard)
- 30 days per request (confirmed)
- All cabins in one response (confirmed)
- Saver vs Standard distinction (via AwardType field)

It does NOT provide (but these are lower priority for MVP):
- Seat counts
- Nonstop vs connecting
- Flight numbers and times
- Booking class codes
