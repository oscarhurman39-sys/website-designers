# England plumber towns: the full list

Written 2026-09-20. This is the town half of `docs/research/mass-plumber-campaign-plan.md`:
the actual list of places to put in `SOURCING_LOCATIONS`, in the exact form the Google Places
query uses. Nothing was changed in the pipeline to produce it; the rollout order and the
arithmetic live in `docs/research/plumber-rollout-plan.md`.

## The short version

- **488 entries in total.** 456 English towns and cities of roughly 20,000 people or more, plus
  the 32 London boroughs (London is one single 8.8M entry in the official list, which is
  useless as a search).
- Each entry is written `Town, County` because that is what the pipeline pastes into the
  search: `plumber in Maidstone, Kent`. Entries are separated by `; ` (semicolon + space) in
  `SOURCING_LOCATIONS`, because a town entry contains a comma of its own.
- **One entry excluded** for being inside the 6-mile home zone around Oxted: `Caterham, Surrey`.
- The paste-ready value is one line of about 12,100 characters. It is also broken into five
  batches further down, matching the rollout stages.

## Where the list comes from

The population cut is the Office for National Statistics 2021 Census "built-up areas" for
England, as tabulated on Wikipedia's *List of ONS built-up areas in England by population*
(read 2026-09-20: https://en.wikipedia.org/wiki/List_of_ONS_built-up_areas_in_England_by_population,
which cites the ONS release *Towns and cities, characteristics of built-up areas, England and
Wales: Census 2021*). The ONS classes 20,000-74,999 as a "medium" built-up area, and the list
stops at exactly that line: the smallest entry is Hazlemere, Buckinghamshire at 20,005 people.
There are 458 of them including London, so 457 towns plus London.

Two things follow from using the ONS definition, and both are worth knowing before you trust
the list:

- **A "built-up area" is a blob of housing, not a council area.** Some famous towns are absent
  because the ONS folded them into a bigger neighbour, and some entries are suburbs rather than
  towns (`Old Trafford, Greater Manchester` is a district of Manchester, not a separate town).
- **Populations are the built-up area, not the council.** They will not match the number on a
  town's own website.

County names are the ceremonial county printed alongside each area in the same source. The
region grouping below is the standard nine ONS regions of England; I moved the Teesside towns
(Middlesbrough, Redcar, Eston, Ingleby Barwick, Thornaby-on-Tees, Stockton-on-Tees, Billingham,
Hartlepool, Darlington) into North East and Grimsby, Cleethorpes and Scunthorpe into Yorkshire
and the Humber, because their ceremonial county and their ONS region disagree. The county
written in the entry itself is unchanged - that is what goes into the search.

London borough populations are 2022 estimates from Wikipedia's *List of London boroughs*
(read 2026-09-20). Distances from Oxted were computed with the same haversine formula the
pipeline uses (`sourcing_agent._miles_between`), from coordinates pulled from Wikipedia.

## What was excluded, and why

**Inside the home zone (6 miles of Oxted, `SOURCING_EXCLUDE_CENTER=51.2572,0.0040`):**

| Entry | Population | Distance from Oxted |
|---|---:|---:|
| Caterham, Surrey | 22,755 | 4.0 miles |

That is the only one. Oxted itself (about 11,000), Limpsfield, Godstone, Westerham, Edenbridge,
Warlingham and Biggin Hill are all under 20,000 people, so they were never in the list to start
with. The next nearest entries are outside the zone and stay in: Redhill 7.7 miles, Sevenoaks
8.1, East Grinstead 8.9, Reigate 9.2, Horley 9.5, Croydon 9.0, Bromley 9.8.

Worth knowing: the 6-mile rule in the code filters **businesses** by their own map pin, not
towns. So a Redhill plumber whose pin happens to sit 5 miles from Oxted is dropped
automatically even though Redhill is in the list. Removing Caterham is about not wasting the
search at all, since most of its plumbers would be thrown away anyway.

**Also excluded: the City of London.** It is a London local authority, but only about 8,600
people live there, which is below the 20,000 cut. The other 32 boroughs are all in.

## The list, by region

"Stage" is the rollout stage from `plumber-rollout-plan.md`. Population is the 2021 built-up
area figure (2022 estimate for London boroughs).

### North East - 30 entries

| Entry | Population | Stage |
|---|---:|:--:|
| Newcastle upon Tyne, Tyne and Wear | 286,445 | 1 |
| Sunderland, Tyne and Wear | 168,315 | 1 |
| Middlesbrough, North Yorkshire | 148,215 | 1 |
| Gateshead, Tyne and Wear | 115,280 | 1 |
| Darlington, County Durham | 93,015 | 1 |
| Hartlepool, County Durham | 87,995 | 1 |
| Stockton-on-Tees, County Durham | 84,815 | 1 |
| South Shields, Tyne and Wear | 73,345 | 2 |
| Tynemouth, Tyne and Wear | 60,605 | 2 |
| Washington, Tyne and Wear | 51,320 | 2 |
| Durham, County Durham | 50,510 | 2 |
| Wallsend, Tyne and Wear | 45,355 | 2 |
| Blyth, Northumberland | 39,730 | 3 |
| Redcar, North Yorkshire | 37,660 | 3 |
| Whitley Bay, Tyne and Wear | 36,880 | 3 |
| Billingham, County Durham | 33,920 | 3 |
| Consett, County Durham | 29,885 | 3 |
| Eston, North Yorkshire | 29,635 | 3 |
| Jarrow, Tyne and Wear | 29,470 | 3 |
| Cramlington, Northumberland | 28,845 | 3 |
| Ashington, Northumberland | 28,280 | 3 |
| Longbenton, Tyne and Wear | 26,880 | 3 |
| Newton Aycliffe, County Durham | 25,765 | 4 |
| Chester-le-Street, County Durham | 23,560 | 4 |
| Ingleby Barwick, North Yorkshire | 23,380 | 4 |
| Bishop Auckland, County Durham | 23,355 | 4 |
| Thornaby-on-Tees, North Yorkshire | 23,350 | 4 |
| Seaham, County Durham | 21,665 | 4 |
| Hebburn, Tyne and Wear | 21,345 | 4 |
| Spennymoor, County Durham | 20,410 | 4 |

### North West - 91 entries

| Entry | Population | Stage |
|---|---:|:--:|
| Liverpool, Merseyside | 506,565 | 1 |
| Manchester, Greater Manchester | 470,405 | 1 |
| Bolton, Greater Manchester | 184,090 | 1 |
| Warrington, Cheshire | 174,970 | 1 |
| Blackpool, Lancashire | 149,070 | 1 |
| Blackburn, Lancashire | 124,955 | 1 |
| Stockport, Greater Manchester | 117,935 | 1 |
| Rochdale, Greater Manchester | 111,255 | 1 |
| Oldham, Greater Manchester | 110,720 | 1 |
| Birkenhead, Merseyside | 109,835 | 1 |
| Salford, Greater Manchester | 108,410 | 1 |
| St Helens, Merseyside | 107,680 | 1 |
| Wythenshawe, Greater Manchester | 97,660 | 1 |
| Preston, Lancashire | 94,490 | 1 |
| Southport, Merseyside | 94,440 | 1 |
| Chester, Cheshire | 92,760 | 1 |
| Wallasey, Merseyside | 85,610 | 1 |
| Wigan, Greater Manchester | 81,580 | 1 |
| Bury, Greater Manchester | 81,095 | 1 |
| Burnley, Lancashire | 78,255 | 1 |
| Carlisle, Cumbria | 77,730 | 1 |
| Crewe, Cheshire | 74,120 | 2 |
| Ellesmere Port, Cheshire | 65,430 | 2 |
| Sale, Greater Manchester | 62,550 | 2 |
| Runcorn, Cheshire | 61,645 | 2 |
| Widnes, Cheshire | 59,935 | 2 |
| Roby, Merseyside | 59,845 | 2 |
| Bebington, Merseyside | 57,600 | 2 |
| Barrow-in-Furness, Cumbria | 55,255 | 2 |
| Macclesfield, Cheshire | 54,345 | 2 |
| Bootle, Merseyside | 53,720 | 2 |
| Lancaster, Lancashire | 52,655 | 2 |
| Crosby, Merseyside | 50,215 | 2 |
| Altrincham, Greater Manchester | 49,680 | 2 |
| Ashton-under-Lyne, Greater Manchester | 48,600 | 2 |
| Middleton, Greater Manchester | 46,630 | 2 |
| Kirkby, Merseyside | 45,560 | 2 |
| Leigh, Greater Manchester | 45,495 | 2 |
| Lytham St Anne's, Lancashire | 42,695 | 3 |
| Urmston, Greater Manchester | 41,740 | 3 |
| Eccles, Greater Manchester | 41,125 | 3 |
| Bamber Bridge, Lancashire | 40,360 | 3 |
| Chorley, Lancashire | 39,535 | 3 |
| Leyland, Lancashire | 39,295 | 3 |
| Prescot, Merseyside | 39,225 | 3 |
| Chadderton, Greater Manchester | 37,610 | 3 |
| Denton, Greater Manchester | 35,995 | 3 |
| Hyde, Greater Manchester | 35,895 | 3 |
| Skelmersdale, Lancashire | 34,915 | 3 |
| Accrington, Lancashire | 34,895 | 3 |
| Fulwood, Lancashire | 34,690 | 3 |
| Nelson, Lancashire | 33,805 | 3 |
| Morecambe, Lancashire | 32,750 | 3 |
| Winsford, Cheshire | 32,530 | 3 |
| Prestwich, Greater Manchester | 31,495 | 3 |
| Radcliffe, Greater Manchester | 31,115 | 3 |
| Congleton, Cheshire | 30,005 | 3 |
| Heywood, Greater Manchester | 29,725 | 3 |
| Heswall, Merseyside | 29,075 | 3 |
| Kendal, Cumbria | 28,940 | 3 |
| Farnworth, Greater Manchester | 28,760 | 3 |
| Stretford, Greater Manchester | 28,015 | 3 |
| Darwen, Lancashire | 27,900 | 3 |
| Ormskirk, Lancashire | 27,715 | 3 |
| Atherton, Greater Manchester | 27,575 | 3 |
| Stalybridge, Greater Manchester | 26,830 | 3 |
| Ashton-in-Makerfield, Greater Manchester | 26,375 | 3 |
| Fleetwood, Lancashire | 26,225 | 3 |
| Little Hulton, Greater Manchester | 25,825 | 4 |
| Wilmslow, Cheshire | 25,725 | 4 |
| Golborne, Greater Manchester | 25,555 | 4 |
| Cheadle Hulme, Greater Manchester | 24,785 | 4 |
| Newton-le-Willows, Merseyside | 24,650 | 4 |
| Hindley, Greater Manchester | 24,490 | 4 |
| Droylsden, Greater Manchester | 23,915 | 4 |
| Rawtenstall, Lancashire | 23,680 | 4 |
| Orrell, Greater Manchester | 23,410 | 4 |
| Royton, Greater Manchester | 22,990 | 4 |
| Whitehaven, Cumbria | 22,945 | 4 |
| Formby, Merseyside | 22,890 | 4 |
| Swinton, Greater Manchester | 22,885 | 4 |
| Reddish, Greater Manchester | 22,200 | 4 |
| Whitefield, Greater Manchester | 22,185 | 4 |
| Westhoughton, Greater Manchester | 21,960 | 4 |
| Old Trafford, Greater Manchester | 21,445 | 4 |
| Workington, Cumbria | 21,275 | 4 |
| Dukinfield, Greater Manchester | 21,155 | 4 |
| Horwich, Greater Manchester | 20,700 | 4 |
| Penwortham, Lancashire | 20,485 | 4 |
| Maghull, Merseyside | 20,370 | 4 |
| Hazel Grove, Greater Manchester | 20,170 | 4 |

### Yorkshire and the Humber - 34 entries

| Entry | Population | Stage |
|---|---:|:--:|
| Leeds, West Yorkshire | 536,280 | 1 |
| Sheffield, South Yorkshire | 500,535 | 1 |
| Bradford, West Yorkshire | 333,950 | 1 |
| Kingston upon Hull, East Riding of Yorkshire | 270,810 | 1 |
| York, North Yorkshire | 141,685 | 1 |
| Huddersfield, West Yorkshire | 141,675 | 1 |
| Wakefield, West Yorkshire | 97,870 | 1 |
| Halifax, West Yorkshire | 88,115 | 1 |
| Doncaster, South Yorkshire | 87,455 | 1 |
| Grimsby, Lincolnshire | 85,925 | 1 |
| Scunthorpe, Lincolnshire | 81,265 | 1 |
| Harrogate, North Yorkshire | 75,515 | 2 |
| Rotherham, South Yorkshire | 71,535 | 2 |
| Barnsley, South Yorkshire | 71,405 | 2 |
| Dewsbury, West Yorkshire | 63,720 | 2 |
| Scarborough, North Yorkshire | 59,505 | 2 |
| Keighley, West Yorkshire | 48,750 | 2 |
| Castleford, West Yorkshire | 45,355 | 2 |
| Batley, West Yorkshire | 44,500 | 2 |
| Pudsey, West Yorkshire | 34,850 | 3 |
| Bridlington, East Riding of Yorkshire | 34,845 | 3 |
| Brighouse, West Yorkshire | 33,160 | 3 |
| Pontefract, West Yorkshire | 32,975 | 3 |
| Morley, West Yorkshire | 32,550 | 3 |
| Beverley, East Riding of Yorkshire | 30,930 | 3 |
| Cleethorpes, Lincolnshire | 29,670 | 3 |
| Shipley, West Yorkshire | 29,225 | 3 |
| Bramley, South Yorkshire | 24,655 | 4 |
| Chapeltown, South Yorkshire | 22,180 | 4 |
| Normanton, West Yorkshire | 21,980 | 4 |
| Ossett, West Yorkshire | 21,855 | 4 |
| Bingley, West Yorkshire | 21,330 | 4 |
| Rothwell, West Yorkshire | 20,965 | 4 |
| Goole, East Riding of Yorkshire | 20,175 | 4 |

### East Midlands - 43 entries

| Entry | Population | Stage |
|---|---:|:--:|
| Leicester, Leicestershire | 406,580 | 1 |
| Nottingham, Nottinghamshire | 299,790 | 1 |
| Derby, Derbyshire | 275,575 | 1 |
| Northampton, Northamptonshire | 243,520 | 1 |
| Lincoln, Lincolnshire | 104,555 | 1 |
| Chesterfield, Derbyshire | 76,420 | 2 |
| Corby, Northamptonshire | 68,160 | 2 |
| Loughborough, Leicestershire | 64,860 | 2 |
| Mansfield, Nottinghamshire | 63,445 | 2 |
| Kettering, Northamptonshire | 63,150 | 2 |
| Wellingborough, Northamptonshire | 54,425 | 2 |
| Carlton, Nottinghamshire | 53,555 | 2 |
| Beeston, Nottinghamshire | 52,355 | 2 |
| Hinckley, Leicestershire | 50,725 | 2 |
| Boston, Lincolnshire | 45,340 | 2 |
| Grantham, Lincolnshire | 44,905 | 2 |
| Worksop, Nottinghamshire | 43,440 | 3 |
| Arnold, Nottinghamshire | 40,010 | 3 |
| Ilkeston, Derbyshire | 38,725 | 3 |
| Long Eaton, Derbyshire | 37,820 | 3 |
| West Bridgford, Nottinghamshire | 36,490 | 3 |
| Sutton in Ashfield, Nottinghamshire | 36,425 | 3 |
| Hucknall, Nottinghamshire | 35,840 | 3 |
| Wigston, Leicestershire | 34,730 | 3 |
| Swadlincote, Derbyshire | 34,565 | 3 |
| Rushden, Northamptonshire | 31,685 | 3 |
| Spalding, Lincolnshire | 30,550 | 3 |
| Newark-on-Trent, Nottinghamshire | 29,755 | 3 |
| Daventry, Northamptonshire | 27,790 | 3 |
| Melton Mowbray, Leicestershire | 27,450 | 3 |
| Heanor, Derbyshire | 24,260 | 4 |
| Market Harborough, Leicestershire | 24,165 | 4 |
| Oadby, Leicestershire | 24,030 | 4 |
| Retford, Nottinghamshire | 23,740 | 4 |
| Clifton, Nottinghamshire | 22,935 | 4 |
| Coalville, Leicestershire | 21,980 | 4 |
| Gainsborough, Lincolnshire | 21,910 | 4 |
| Kirkby-in-Ashfield, Nottinghamshire | 21,270 | 4 |
| Dronfield, Derbyshire | 21,160 | 4 |
| Stamford, Lincolnshire | 20,750 | 4 |
| Skegness, Lincolnshire | 20,700 | 4 |
| Ripley, Derbyshire | 20,180 | 4 |
| Buxton, Derbyshire | 20,050 | 4 |

### West Midlands - 51 entries

| Entry | Population | Stage |
|---|---:|:--:|
| Birmingham, West Midlands | 1,121,375 | 1 |
| Coventry, West Midlands | 344,285 | 1 |
| Stoke-on-Trent, Staffordshire | 260,560 | 1 |
| Wolverhampton, West Midlands | 234,025 | 1 |
| Telford, Shropshire | 156,910 | 1 |
| Solihull, West Midlands | 107,735 | 1 |
| Worcester, Worcestershire | 105,465 | 1 |
| West Bromwich, West Midlands | 103,110 | 1 |
| Sutton Coldfield, West Midlands | 93,375 | 1 |
| Nuneaton, Warwickshire | 88,815 | 1 |
| Redditch, Worcestershire | 81,635 | 1 |
| Rugby, Warwickshire | 78,120 | 1 |
| Newcastle-under-Lyme, Staffordshire | 76,505 | 2 |
| Burton upon Trent, Staffordshire | 76,255 | 2 |
| Tamworth, Staffordshire | 76,090 | 2 |
| Shrewsbury, Shropshire | 76,015 | 2 |
| Stafford, Staffordshire | 71,690 | 2 |
| Walsall, West Midlands | 70,775 | 2 |
| Dudley, West Midlands | 64,270 | 2 |
| Cannock, Staffordshire | 63,065 | 2 |
| Hereford, Herefordshire | 60,480 | 2 |
| Halesowen, West Midlands | 60,110 | 2 |
| Kidderminster, Worcestershire | 57,560 | 2 |
| Stourbridge, West Midlands | 56,950 | 2 |
| Smethwick, West Midlands | 56,340 | 2 |
| Kingswinford, West Midlands | 51,910 | 2 |
| Bloxwich, West Midlands | 51,875 | 2 |
| Royal Leamington Spa, Warwickshire | 51,310 | 2 |
| Willenhall, West Midlands | 49,580 | 2 |
| Tipton, West Midlands | 47,200 | 2 |
| Oldbury, West Midlands | 45,180 | 2 |
| Rowley Regis, West Midlands | 39,050 | 3 |
| Warwick, Warwickshire | 36,680 | 3 |
| Bromsgrove, Worcestershire | 34,755 | 3 |
| Bilston, West Midlands | 34,640 | 3 |
| Great Malvern, Worcestershire | 33,185 | 3 |
| Lichfield, Staffordshire | 32,580 | 3 |
| Brierley Hill, West Midlands | 32,305 | 3 |
| Sedgley, West Midlands | 31,990 | 3 |
| Bedworth, Warwickshire | 31,090 | 3 |
| Evesham, Worcestershire | 28,250 | 3 |
| Stratford-upon-Avon, Warwickshire | 28,120 | 3 |
| Burntwood, Staffordshire | 27,900 | 3 |
| Droitwich Spa, Worcestershire | 26,420 | 3 |
| Rugeley, Staffordshire | 26,145 | 3 |
| Coseley, West Midlands | 25,205 | 4 |
| Kenilworth, Warwickshire | 22,235 | 4 |
| Darlaston, West Midlands | 21,540 | 4 |
| Brownhills, West Midlands | 21,240 | 4 |
| Wednesbury, West Midlands | 20,315 | 4 |
| Stourport-on-Severn, Worcestershire | 20,305 | 4 |

### East of England - 64 entries

| Entry | Population | Stage |
|---|---:|:--:|
| Luton, Bedfordshire | 233,525 | 1 |
| Norwich, Norfolk | 200,770 | 1 |
| Peterborough, Cambridgeshire | 190,605 | 1 |
| Southend-on-Sea, Essex | 182,305 | 1 |
| Cambridge, Cambridgeshire | 152,740 | 1 |
| Ipswich, Suffolk | 151,565 | 1 |
| Watford, Hertfordshire | 131,325 | 1 |
| Colchester, Essex | 130,245 | 1 |
| Basildon, Essex | 115,955 | 1 |
| Chelmsford, Essex | 110,625 | 1 |
| Bedford, Bedfordshire | 97,235 | 1 |
| Hemel Hempstead, Hertfordshire | 95,985 | 1 |
| Stevenage, Hertfordshire | 94,470 | 1 |
| Harlow, Essex | 93,580 | 1 |
| St Albans, Hertfordshire | 75,540 | 2 |
| Lowestoft, Suffolk | 71,315 | 2 |
| Brentwood, Essex | 55,340 | 2 |
| Clacton-on-Sea, Essex | 53,200 | 2 |
| Welwyn Garden City, Hertfordshire | 51,505 | 2 |
| Thundersley, Essex | 49,885 | 2 |
| King's Lynn, Norfolk | 47,615 | 2 |
| Grays, Essex | 44,345 | 2 |
| Cheshunt, Hertfordshire | 43,680 | 3 |
| Braintree, Essex | 43,190 | 3 |
| Leighton Buzzard, Bedfordshire | 42,735 | 3 |
| Hatfield, Hertfordshire | 41,560 | 3 |
| Bury St Edmunds, Suffolk | 41,280 | 3 |
| Bishop's Stortford, Hertfordshire | 40,915 | 3 |
| Hoddesdon, Hertfordshire | 40,615 | 3 |
| Borehamwood, Hertfordshire | 39,765 | 3 |
| Canvey Island, Essex | 38,010 | 3 |
| Hitchin, Hertfordshire | 35,220 | 3 |
| Dunstable, Bedfordshire | 34,500 | 3 |
| Billericay, Essex | 34,075 | 3 |
| Letchworth Garden City, Hertfordshire | 33,990 | 3 |
| Loughton, Essex | 33,345 | 3 |
| St Neots, Cambridgeshire | 33,265 | 3 |
| Rayleigh, Essex | 32,380 | 3 |
| Harpenden, Hertfordshire | 30,965 | 3 |
| Stanford-le-Hope, Essex | 29,525 | 3 |
| Great Yarmouth, Norfolk | 28,985 | 3 |
| Hertford, Hertfordshire | 28,800 | 3 |
| Bushey, Hertfordshire | 28,425 | 3 |
| Wickford, Essex | 27,535 | 3 |
| Witham, Essex | 27,395 | 3 |
| Wisbech, Cambridgeshire | 26,795 | 3 |
| Haverhill, Suffolk | 26,705 | 3 |
| Rickmansworth, Hertfordshire | 26,290 | 3 |
| Huntingdon, Cambridgeshire | 25,600 | 4 |
| Thetford, Norfolk | 25,510 | 4 |
| Gorleston-on-Sea, Norfolk | 24,470 | 4 |
| Felixstowe, Suffolk | 24,220 | 4 |
| Sudbury, Suffolk | 23,920 | 4 |
| Chafford Hundred, Essex | 23,585 | 4 |
| Potters Bar, Hertfordshire | 23,400 | 4 |
| Maldon, Essex | 23,380 | 4 |
| Kempston, Bedfordshire | 22,785 | 4 |
| South Ockendon, Essex | 22,440 | 4 |
| Biggleswade, Bedfordshire | 21,950 | 4 |
| Stowmarket, Suffolk | 21,535 | 4 |
| March, Cambridgeshire | 21,345 | 4 |
| Berkhamsted, Hertfordshire | 21,240 | 4 |
| Dereham, Norfolk | 20,785 | 4 |
| Harwich, Essex | 20,215 | 4 |

### South East - 105 entries

| Entry | Population | Stage |
|---|---:|:--:|
| Brighton and Hove, East Sussex | 277,105 | 1 |
| Milton Keynes, Buckinghamshire | 264,349 | 1 |
| Southampton, Hampshire | 249,620 | 1 |
| Portsmouth, Hampshire | 223,305 | 1 |
| Reading, Berkshire | 203,795 | 1 |
| Oxford, Oxfordshire | 170,805 | 1 |
| Slough, Berkshire | 166,855 | 1 |
| Crawley, West Sussex | 120,550 | 1 |
| Basingstoke, Hampshire | 117,210 | 1 |
| Worthing, West Sussex | 111,620 | 1 |
| Maidstone, Kent | 109,490 | 1 |
| Gillingham, Kent | 108,480 | 1 |
| Eastbourne, East Sussex | 99,180 | 1 |
| Hastings, East Sussex | 91,490 | 1 |
| Aylesbury, Buckinghamshire | 87,950 | 1 |
| High Wycombe, Buckinghamshire | 83,535 | 1 |
| Ashford, Kent | 82,140 | 1 |
| Bracknell, Berkshire | 78,675 | 1 |
| Guildford, Surrey | 77,880 | 1 |
| Chatham, Kent | 76,955 | 2 |
| Woking, Surrey | 75,660 | 2 |
| Gosport, Hampshire | 70,110 | 2 |
| Dartford, Kent | 69,130 | 2 |
| Bognor Regis, West Sussex | 68,435 | 2 |
| Maidenhead, Berkshire | 67,375 | 2 |
| Rochester, Kent | 67,285 | 2 |
| Margate, Kent | 63,320 | 2 |
| Farnborough, Hampshire | 60,655 | 2 |
| Gravesend, Kent | 58,105 | 2 |
| Canterbury, Kent | 55,090 | 2 |
| Sittingbourne, Kent | 54,390 | 2 |
| Banbury, Oxfordshire | 52,045 | 2 |
| Folkestone, Kent | 51,995 | 2 |
| Tunbridge Wells, Kent | 51,220 | 2 |
| Wokingham, Berkshire | 50,325 | 2 |
| Horsham, West Sussex | 50,215 | 2 |
| Andover, Hampshire | 48,480 | 2 |
| Winchester, Hampshire | 48,480 | 2 |
| Eastleigh, Hampshire | 48,360 | 2 |
| West Molesey, Surrey | 47,150 | 2 |
| Havant, Hampshire | 46,960 | 2 |
| Bletchley, Buckinghamshire | 45,010 | 2 |
| Bexhill-on-Sea, East Sussex | 43,755 | 2 |
| Fareham, Hampshire | 42,625 | 3 |
| Newbury, Berkshire | 42,260 | 3 |
| Ramsgate, Kent | 42,030 | 3 |
| Haywards Heath, West Sussex | 40,185 | 3 |
| Aldershot, Hampshire | 39,825 | 3 |
| Fleet, Hampshire | 37,790 | 3 |
| Bicester, Oxfordshire | 37,755 | 3 |
| Camberley, Surrey | 36,785 | 3 |
| Dover, Kent | 36,360 | 3 |
| Tonbridge, Kent | 36,125 | 3 |
| Epsom, Surrey | 35,850 | 3 |
| Locks Heath, Hampshire | 35,755 | 3 |
| Waterlooville, Hampshire | 34,775 | 3 |
| Didcot, Oxfordshire | 34,600 | 3 |
| Horndean, Hampshire | 34,050 | 3 |
| Rustington, West Sussex | 33,885 | 3 |
| Burgess Hill, West Sussex | 33,355 | 3 |
| Abingdon-on-Thames, Oxfordshire | 33,175 | 3 |
| Caversham, Berkshire | 33,040 | 3 |
| New Haw, Surrey | 33,035 | 3 |
| Redhill, Surrey | 32,525 | 3 |
| Whitstable, Kent | 32,195 | 3 |
| Chandler's Ford, Hampshire | 31,975 | 3 |
| Chichester, West Sussex | 31,710 | 3 |
| Windsor, Berkshire | 31,560 | 3 |
| Witney, Oxfordshire | 30,165 | 3 |
| Northfleet, Kent | 29,900 | 3 |
| Deal, Kent | 28,985 | 3 |
| Totton, Hampshire | 28,090 | 3 |
| Woodley, Berkshire | 28,025 | 3 |
| Egham, Surrey | 28,000 | 3 |
| Lancing, West Sussex | 27,930 | 3 |
| Ewell, Surrey | 27,515 | 3 |
| Tadworth, Surrey | 27,095 | 3 |
| Horley, Surrey | 27,070 | 3 |
| Walton-on-Thames, Surrey | 27,020 | 3 |
| Sevenoaks, Kent | 26,475 | 3 |
| East Grinstead, West Sussex | 26,350 | 3 |
| Larkfield, Kent | 26,150 | 3 |
| Broadstairs, Kent | 25,775 | 4 |
| Thatcham, Berkshire | 25,550 | 4 |
| Newport, Isle of Wight | 25,405 | 4 |
| Herne Bay, Kent | 24,870 | 4 |
| Lee-on-the-Solent, Hampshire | 24,315 | 4 |
| Ash, Surrey | 24,285 | 4 |
| Ryde, Isle of Wight | 24,105 | 4 |
| Ascot, Berkshire | 23,995 | 4 |
| Seaford, East Sussex | 23,865 | 4 |
| Reigate, Surrey | 23,780 | 4 |
| Chesham, Buckinghamshire | 23,695 | 4 |
| Shoreham-by-Sea, West Sussex | 23,660 | 4 |
| Hedge End, Hampshire | 23,190 | 4 |
| Ashford, Surrey | 22,825 | 4 |
| Hailsham, East Sussex | 22,550 | 4 |
| Crowborough, East Sussex | 21,990 | 4 |
| Great Bookham, Surrey | 21,655 | 4 |
| Sunbury-on-Thames, Surrey | 21,475 | 4 |
| Staines-upon-Thames, Surrey | 21,325 | 4 |
| Faversham, Kent | 20,940 | 4 |
| Farnham, Surrey | 20,500 | 4 |
| Sandhurst, Berkshire | 20,215 | 4 |
| Hazlemere, Buckinghamshire | 20,005 | 4 |

### South West - 38 entries

| Entry | Population | Stage |
|---|---:|:--:|
| Bristol, Bristol | 425,215 | 1 |
| Plymouth, Devon | 266,955 | 1 |
| Bournemouth, Dorset | 196,455 | 1 |
| Swindon, Wiltshire | 183,680 | 1 |
| Kingswood, South Gloucestershire | 160,270 | 1 |
| Poole, Dorset | 141,005 | 1 |
| Exeter, Devon | 126,175 | 1 |
| Gloucester, Gloucestershire | 118,555 | 1 |
| Cheltenham, Gloucestershire | 115,940 | 1 |
| Bath, Somerset | 94,080 | 1 |
| Weston-super-Mare, Somerset | 84,605 | 1 |
| Paignton, Devon | 67,520 | 2 |
| Taunton, Somerset | 61,665 | 2 |
| Weymouth, Dorset | 55,535 | 2 |
| Torquay, Devon | 52,035 | 2 |
| Yeovil, Somerset | 50,170 | 2 |
| Christchurch, Dorset | 48,985 | 2 |
| Bridgwater, Somerset | 47,860 | 2 |
| Salisbury, Wiltshire | 47,690 | 2 |
| Trowbridge, Wiltshire | 43,750 | 3 |
| Chippenham, Wiltshire | 36,090 | 3 |
| Exmouth, Devon | 35,500 | 3 |
| Barnstaple, Devon | 31,275 | 3 |
| Newton Abbot, Devon | 29,650 | 3 |
| Yate, Gloucestershire | 28,350 | 3 |
| Frome, Somerset | 27,905 | 3 |
| Portishead, Somerset | 26,355 | 3 |
| Stroud, Gloucestershire | 26,080 | 3 |
| Bradley Stoke, Gloucestershire | 25,200 | 4 |
| Newquay, Cornwall | 24,545 | 4 |
| St Austell, Cornwall | 24,360 | 4 |
| Falmouth, Cornwall | 24,070 | 4 |
| Truro, Cornwall | 23,060 | 4 |
| Dorchester, Dorset | 21,360 | 4 |
| Hardwicke, Gloucestershire | 21,125 | 4 |
| Clevedon, Somerset | 21,085 | 4 |
| Stratton St Margaret, Wiltshire | 20,690 | 4 |
| Camborne, Cornwall | 20,450 | 4 |

### London - 32 entries

All 32 boroughs. These are administrative areas rather than towns, and they are the most
competitive plumbing market in the country, so they are a separate opt-in stage.

| Entry | Population | Stage |
|---|---:|:--:|
| Croydon, Greater London | 392,224 | 5 |
| Barnet, Greater London | 389,101 | 5 |
| Ealing, Greater London | 369,937 | 5 |
| Newham, Greater London | 358,645 | 5 |
| Brent, Greater London | 341,221 | 5 |
| Bromley, Greater London | 329,578 | 5 |
| Wandsworth, Greater London | 329,035 | 5 |
| Enfield, Greater London | 327,224 | 5 |
| Tower Hamlets, Greater London | 325,789 | 5 |
| Lambeth, Greater London | 316,812 | 5 |
| Southwark, Greater London | 311,913 | 5 |
| Redbridge, Greater London | 310,911 | 5 |
| Hillingdon, Greater London | 310,681 | 5 |
| Lewisham, Greater London | 298,653 | 5 |
| Greenwich, Greater London | 291,080 | 5 |
| Hounslow, Greater London | 290,488 | 5 |
| Waltham Forest, Greater London | 275,887 | 5 |
| Havering, Greater London | 264,703 | 5 |
| Haringey, Greater London | 261,811 | 5 |
| Hackney, Greater London | 261,491 | 5 |
| Harrow, Greater London | 261,185 | 5 |
| Bexley, Greater London | 247,835 | 5 |
| Islington, Greater London | 220,373 | 5 |
| Barking and Dagenham, Greater London | 219,992 | 5 |
| Camden, Greater London | 218,049 | 5 |
| Westminster, Greater London | 211,365 | 5 |
| Merton, Greater London | 210,709 | 5 |
| Sutton, Greater London | 209,602 | 5 |
| Richmond upon Thames, Greater London | 194,894 | 5 |
| Hammersmith and Fulham, Greater London | 185,238 | 5 |
| Kingston upon Thames, Greater London | 168,302 | 5 |
| Kensington and Chelsea, Greater London | 146,154 | 5 |

**Grand total: 488 entries.**

| Region | Entries |
|---|---:|
| North East | 30 |
| North West | 91 |
| Yorkshire and the Humber | 34 |
| East Midlands | 43 |
| West Midlands | 51 |
| East of England | 64 |
| South East | 105 |
| South West | 38 |
| London | 32 |
| **Total** | **488** |

## Ready to paste: `SOURCING_LOCATIONS`

One line, entries separated by `; `. In `.env` this has to stay on a **single physical line**
- it is about 12,120 characters long. `pipeline/config.py` reads one variable named
`SOURCING_LOCATIONS` and splits it on `;`, so there is no way to spread it over several lines
or several variables without a code change.

Do not paste the whole thing on day one. The staged batches below are the same list cut into
the rollout stages; `plumber-rollout-plan.md` says why, and what to check between stages.

### Everything (all 488 entries)

```
SOURCING_LOCATIONS=Birmingham, West Midlands; Leeds, West Yorkshire; Liverpool, Merseyside; Sheffield, South Yorkshire; Manchester, Greater Manchester; Bristol, Bristol; Leicester, Leicestershire; Coventry, West Midlands; Bradford, West Yorkshire; Nottingham, Nottinghamshire; Newcastle upon Tyne, Tyne and Wear; Brighton and Hove, East Sussex; Derby, Derbyshire; Kingston upon Hull, East Riding of Yorkshire; Plymouth, Devon; Milton Keynes, Buckinghamshire; Stoke-on-Trent, Staffordshire; Southampton, Hampshire; Northampton, Northamptonshire; Wolverhampton, West Midlands; Luton, Bedfordshire; Portsmouth, Hampshire; Reading, Berkshire; Norwich, Norfolk; Bournemouth, Dorset; Peterborough, Cambridgeshire; Bolton, Greater Manchester; Swindon, Wiltshire; Southend-on-Sea, Essex; Warrington, Cheshire; Oxford, Oxfordshire; Sunderland, Tyne and Wear; Slough, Berkshire; Kingswood, South Gloucestershire; Telford, Shropshire; Cambridge, Cambridgeshire; Ipswich, Suffolk; Blackpool, Lancashire; Middlesbrough, North Yorkshire; York, North Yorkshire; Huddersfield, West Yorkshire; Poole, Dorset; Watford, Hertfordshire; Colchester, Essex; Exeter, Devon; Blackburn, Lancashire; Crawley, West Sussex; Gloucester, Gloucestershire; Stockport, Greater Manchester; Basingstoke, Hampshire; Basildon, Essex; Cheltenham, Gloucestershire; Gateshead, Tyne and Wear; Worthing, West Sussex; Rochdale, Greater Manchester; Oldham, Greater Manchester; Chelmsford, Essex; Birkenhead, Merseyside; Maidstone, Kent; Gillingham, Kent; Salford, Greater Manchester; Solihull, West Midlands; St Helens, Merseyside; Worcester, Worcestershire; Lincoln, Lincolnshire; West Bromwich, West Midlands; Eastbourne, East Sussex; Wakefield, West Yorkshire; Wythenshawe, Greater Manchester; Bedford, Bedfordshire; Hemel Hempstead, Hertfordshire; Preston, Lancashire; Stevenage, Hertfordshire; Southport, Merseyside; Bath, Somerset; Harlow, Essex; Sutton Coldfield, West Midlands; Darlington, County Durham; Chester, Cheshire; Hastings, East Sussex; Nuneaton, Warwickshire; Halifax, West Yorkshire; Hartlepool, County Durham; Aylesbury, Buckinghamshire; Doncaster, South Yorkshire; Grimsby, Lincolnshire; Wallasey, Merseyside; Stockton-on-Tees, County Durham; Weston-super-Mare, Somerset; High Wycombe, Buckinghamshire; Ashford, Kent; Redditch, Worcestershire; Wigan, Greater Manchester; Scunthorpe, Lincolnshire; Bury, Greater Manchester; Bracknell, Berkshire; Burnley, Lancashire; Rugby, Warwickshire; Guildford, Surrey; Carlisle, Cumbria; Chatham, Kent; Newcastle-under-Lyme, Staffordshire; Chesterfield, Derbyshire; Burton upon Trent, Staffordshire; Tamworth, Staffordshire; Shrewsbury, Shropshire; Woking, Surrey; St Albans, Hertfordshire; Harrogate, North Yorkshire; Crewe, Cheshire; South Shields, Tyne and Wear; Stafford, Staffordshire; Rotherham, South Yorkshire; Barnsley, South Yorkshire; Lowestoft, Suffolk; Walsall, West Midlands; Gosport, Hampshire; Dartford, Kent; Bognor Regis, West Sussex; Corby, Northamptonshire; Paignton, Devon; Maidenhead, Berkshire; Rochester, Kent; Ellesmere Port, Cheshire; Loughborough, Leicestershire; Dudley, West Midlands; Dewsbury, West Yorkshire; Mansfield, Nottinghamshire; Margate, Kent; Kettering, Northamptonshire; Cannock, Staffordshire; Sale, Greater Manchester; Taunton, Somerset; Runcorn, Cheshire; Farnborough, Hampshire; Tynemouth, Tyne and Wear; Hereford, Herefordshire; Halesowen, West Midlands; Widnes, Cheshire; Roby, Merseyside; Scarborough, North Yorkshire; Gravesend, Kent; Bebington, Merseyside; Kidderminster, Worcestershire; Stourbridge, West Midlands; Smethwick, West Midlands; Weymouth, Dorset; Brentwood, Essex; Barrow-in-Furness, Cumbria; Canterbury, Kent; Wellingborough, Northamptonshire; Sittingbourne, Kent; Macclesfield, Cheshire; Bootle, Merseyside; Carlton, Nottinghamshire; Clacton-on-Sea, Essex; Lancaster, Lancashire; Beeston, Nottinghamshire; Banbury, Oxfordshire; Torquay, Devon; Folkestone, Kent; Kingswinford, West Midlands; Bloxwich, West Midlands; Welwyn Garden City, Hertfordshire; Washington, Tyne and Wear; Royal Leamington Spa, Warwickshire; Tunbridge Wells, Kent; Hinckley, Leicestershire; Durham, County Durham; Wokingham, Berkshire; Crosby, Merseyside; Horsham, West Sussex; Yeovil, Somerset; Thundersley, Essex; Altrincham, Greater Manchester; Willenhall, West Midlands; Christchurch, Dorset; Keighley, West Yorkshire; Ashton-under-Lyne, Greater Manchester; Andover, Hampshire; Winchester, Hampshire; Eastleigh, Hampshire; Bridgwater, Somerset; Salisbury, Wiltshire; King's Lynn, Norfolk; Tipton, West Midlands; West Molesey, Surrey; Havant, Hampshire; Middleton, Greater Manchester; Kirkby, Merseyside; Leigh, Greater Manchester; Castleford, West Yorkshire; Wallsend, Tyne and Wear; Boston, Lincolnshire; Oldbury, West Midlands; Bletchley, Buckinghamshire; Grantham, Lincolnshire; Batley, West Yorkshire; Grays, Essex; Bexhill-on-Sea, East Sussex; Trowbridge, Wiltshire; Cheshunt, Hertfordshire; Worksop, Nottinghamshire; Braintree, Essex; Leighton Buzzard, Bedfordshire; Lytham St Anne's, Lancashire; Fareham, Hampshire; Newbury, Berkshire; Ramsgate, Kent; Urmston, Greater Manchester; Hatfield, Hertfordshire; Bury St Edmunds, Suffolk; Eccles, Greater Manchester; Bishop's Stortford, Hertfordshire; Hoddesdon, Hertfordshire; Bamber Bridge, Lancashire; Haywards Heath, West Sussex; Arnold, Nottinghamshire; Aldershot, Hampshire; Borehamwood, Hertfordshire; Blyth, Northumberland; Chorley, Lancashire; Leyland, Lancashire; Prescot, Merseyside; Rowley Regis, West Midlands; Ilkeston, Derbyshire; Canvey Island, Essex; Long Eaton, Derbyshire; Fleet, Hampshire; Bicester, Oxfordshire; Redcar, North Yorkshire; Chadderton, Greater Manchester; Whitley Bay, Tyne and Wear; Camberley, Surrey; Warwick, Warwickshire; West Bridgford, Nottinghamshire; Sutton in Ashfield, Nottinghamshire; Dover, Kent; Tonbridge, Kent; Chippenham, Wiltshire; Denton, Greater Manchester; Hyde, Greater Manchester; Epsom, Surrey; Hucknall, Nottinghamshire; Locks Heath, Hampshire; Exmouth, Devon; Hitchin, Hertfordshire; Skelmersdale, Lancashire; Accrington, Lancashire; Pudsey, West Yorkshire; Bridlington, East Riding of Yorkshire; Waterlooville, Hampshire; Bromsgrove, Worcestershire; Wigston, Leicestershire; Fulwood, Lancashire; Bilston, West Midlands; Didcot, Oxfordshire; Swadlincote, Derbyshire; Dunstable, Bedfordshire; Billericay, Essex; Horndean, Hampshire; Letchworth Garden City, Hertfordshire; Billingham, County Durham; Rustington, West Sussex; Nelson, Lancashire; Burgess Hill, West Sussex; Loughton, Essex; St Neots, Cambridgeshire; Great Malvern, Worcestershire; Abingdon-on-Thames, Oxfordshire; Brighouse, West Yorkshire; Caversham, Berkshire; New Haw, Surrey; Pontefract, West Yorkshire; Morecambe, Lancashire; Lichfield, Staffordshire; Morley, West Yorkshire; Winsford, Cheshire; Redhill, Surrey; Rayleigh, Essex; Brierley Hill, West Midlands; Whitstable, Kent; Sedgley, West Midlands; Chandler's Ford, Hampshire; Chichester, West Sussex; Rushden, Northamptonshire; Windsor, Berkshire; Prestwich, Greater Manchester; Barnstaple, Devon; Radcliffe, Greater Manchester; Bedworth, Warwickshire; Harpenden, Hertfordshire; Beverley, East Riding of Yorkshire; Spalding, Lincolnshire; Witney, Oxfordshire; Congleton, Cheshire; Northfleet, Kent; Consett, County Durham; Newark-on-Trent, Nottinghamshire; Heywood, Greater Manchester; Cleethorpes, Lincolnshire; Newton Abbot, Devon; Eston, North Yorkshire; Stanford-le-Hope, Essex; Jarrow, Tyne and Wear; Shipley, West Yorkshire; Heswall, Merseyside; Deal, Kent; Great Yarmouth, Norfolk; Kendal, Cumbria; Cramlington, Northumberland; Hertford, Hertfordshire; Farnworth, Greater Manchester; Bushey, Hertfordshire; Yate, Gloucestershire; Ashington, Northumberland; Evesham, Worcestershire; Stratford-upon-Avon, Warwickshire; Totton, Hampshire; Woodley, Berkshire; Stretford, Greater Manchester; Egham, Surrey; Lancing, West Sussex; Frome, Somerset; Burntwood, Staffordshire; Darwen, Lancashire; Daventry, Northamptonshire; Ormskirk, Lancashire; Atherton, Greater Manchester; Wickford, Essex; Ewell, Surrey; Melton Mowbray, Leicestershire; Witham, Essex; Tadworth, Surrey; Horley, Surrey; Walton-on-Thames, Surrey; Longbenton, Tyne and Wear; Stalybridge, Greater Manchester; Wisbech, Cambridgeshire; Haverhill, Suffolk; Sevenoaks, Kent; Droitwich Spa, Worcestershire; Ashton-in-Makerfield, Greater Manchester; Portishead, Somerset; East Grinstead, West Sussex; Rickmansworth, Hertfordshire; Fleetwood, Lancashire; Larkfield, Kent; Rugeley, Staffordshire; Stroud, Gloucestershire; Little Hulton, Greater Manchester; Broadstairs, Kent; Newton Aycliffe, County Durham; Wilmslow, Cheshire; Huntingdon, Cambridgeshire; Golborne, Greater Manchester; Thatcham, Berkshire; Thetford, Norfolk; Newport, Isle of Wight; Coseley, West Midlands; Bradley Stoke, Gloucestershire; Herne Bay, Kent; Cheadle Hulme, Greater Manchester; Bramley, South Yorkshire; Newton-le-Willows, Merseyside; Newquay, Cornwall; Hindley, Greater Manchester; Gorleston-on-Sea, Norfolk; St Austell, Cornwall; Lee-on-the-Solent, Hampshire; Ash, Surrey; Heanor, Derbyshire; Felixstowe, Suffolk; Market Harborough, Leicestershire; Ryde, Isle of Wight; Falmouth, Cornwall; Oadby, Leicestershire; Ascot, Berkshire; Sudbury, Suffolk; Droylsden, Greater Manchester; Seaford, East Sussex; Reigate, Surrey; Retford, Nottinghamshire; Chesham, Buckinghamshire; Rawtenstall, Lancashire; Shoreham-by-Sea, West Sussex; Chafford Hundred, Essex; Chester-le-Street, County Durham; Orrell, Greater Manchester; Potters Bar, Hertfordshire; Ingleby Barwick, North Yorkshire; Maldon, Essex; Bishop Auckland, County Durham; Thornaby-on-Tees, North Yorkshire; Hedge End, Hampshire; Truro, Cornwall; Royton, Greater Manchester; Whitehaven, Cumbria; Clifton, Nottinghamshire; Formby, Merseyside; Swinton, Greater Manchester; Ashford, Surrey; Kempston, Bedfordshire; Hailsham, East Sussex; South Ockendon, Essex; Kenilworth, Warwickshire; Reddish, Greater Manchester; Whitefield, Greater Manchester; Chapeltown, South Yorkshire; Crowborough, East Sussex; Coalville, Leicestershire; Normanton, West Yorkshire; Westhoughton, Greater Manchester; Biggleswade, Bedfordshire; Gainsborough, Lincolnshire; Ossett, West Yorkshire; Seaham, County Durham; Great Bookham, Surrey; Darlaston, West Midlands; Stowmarket, Suffolk; Sunbury-on-Thames, Surrey; Old Trafford, Greater Manchester; Dorchester, Dorset; Hebburn, Tyne and Wear; March, Cambridgeshire; Bingley, West Yorkshire; Staines-upon-Thames, Surrey; Workington, Cumbria; Kirkby-in-Ashfield, Nottinghamshire; Berkhamsted, Hertfordshire; Brownhills, West Midlands; Dronfield, Derbyshire; Dukinfield, Greater Manchester; Hardwicke, Gloucestershire; Clevedon, Somerset; Rothwell, West Yorkshire; Faversham, Kent; Dereham, Norfolk; Stamford, Lincolnshire; Horwich, Greater Manchester; Skegness, Lincolnshire; Stratton St Margaret, Wiltshire; Farnham, Surrey; Penwortham, Lancashire; Camborne, Cornwall; Spennymoor, County Durham; Maghull, Merseyside; Wednesbury, West Midlands; Stourport-on-Severn, Worcestershire; Harwich, Essex; Sandhurst, Berkshire; Ripley, Derbyshire; Goole, East Riding of Yorkshire; Hazel Grove, Greater Manchester; Buxton, Derbyshire; Hazlemere, Buckinghamshire; Croydon, Greater London; Barnet, Greater London; Ealing, Greater London; Newham, Greater London; Brent, Greater London; Bromley, Greater London; Wandsworth, Greater London; Enfield, Greater London; Tower Hamlets, Greater London; Lambeth, Greater London; Southwark, Greater London; Redbridge, Greater London; Hillingdon, Greater London; Lewisham, Greater London; Greenwich, Greater London; Hounslow, Greater London; Waltham Forest, Greater London; Havering, Greater London; Haringey, Greater London; Hackney, Greater London; Harrow, Greater London; Bexley, Greater London; Islington, Greater London; Barking and Dagenham, Greater London; Camden, Greater London; Westminster, Greater London; Merton, Greater London; Sutton, Greater London; Richmond upon Thames, Greater London; Hammersmith and Fulham, Greater London; Kingston upon Thames, Greater London; Kensington and Chelsea, Greater London
```

### The same list in staged batches

Each stage is **cumulative**: at stage 2 the value is stage 1 + stage 2 joined with `; `.

#### Stage 1 - the 100 largest towns outside London (100 entries)

Birmingham (1,121,375) down to Carlisle (77,730).

```
Birmingham, West Midlands; Leeds, West Yorkshire; Liverpool, Merseyside; Sheffield, South Yorkshire; Manchester, Greater Manchester; Bristol, Bristol; Leicester, Leicestershire; Coventry, West Midlands; Bradford, West Yorkshire; Nottingham, Nottinghamshire; Newcastle upon Tyne, Tyne and Wear; Brighton and Hove, East Sussex; Derby, Derbyshire; Kingston upon Hull, East Riding of Yorkshire; Plymouth, Devon; Milton Keynes, Buckinghamshire; Stoke-on-Trent, Staffordshire; Southampton, Hampshire; Northampton, Northamptonshire; Wolverhampton, West Midlands; Luton, Bedfordshire; Portsmouth, Hampshire; Reading, Berkshire; Norwich, Norfolk; Bournemouth, Dorset; Peterborough, Cambridgeshire; Bolton, Greater Manchester; Swindon, Wiltshire; Southend-on-Sea, Essex; Warrington, Cheshire; Oxford, Oxfordshire; Sunderland, Tyne and Wear; Slough, Berkshire; Kingswood, South Gloucestershire; Telford, Shropshire; Cambridge, Cambridgeshire; Ipswich, Suffolk; Blackpool, Lancashire; Middlesbrough, North Yorkshire; York, North Yorkshire; Huddersfield, West Yorkshire; Poole, Dorset; Watford, Hertfordshire; Colchester, Essex; Exeter, Devon; Blackburn, Lancashire; Crawley, West Sussex; Gloucester, Gloucestershire; Stockport, Greater Manchester; Basingstoke, Hampshire; Basildon, Essex; Cheltenham, Gloucestershire; Gateshead, Tyne and Wear; Worthing, West Sussex; Rochdale, Greater Manchester; Oldham, Greater Manchester; Chelmsford, Essex; Birkenhead, Merseyside; Maidstone, Kent; Gillingham, Kent; Salford, Greater Manchester; Solihull, West Midlands; St Helens, Merseyside; Worcester, Worcestershire; Lincoln, Lincolnshire; West Bromwich, West Midlands; Eastbourne, East Sussex; Wakefield, West Yorkshire; Wythenshawe, Greater Manchester; Bedford, Bedfordshire; Hemel Hempstead, Hertfordshire; Preston, Lancashire; Stevenage, Hertfordshire; Southport, Merseyside; Bath, Somerset; Harlow, Essex; Sutton Coldfield, West Midlands; Darlington, County Durham; Chester, Cheshire; Hastings, East Sussex; Nuneaton, Warwickshire; Halifax, West Yorkshire; Hartlepool, County Durham; Aylesbury, Buckinghamshire; Doncaster, South Yorkshire; Grimsby, Lincolnshire; Wallasey, Merseyside; Stockton-on-Tees, County Durham; Weston-super-Mare, Somerset; High Wycombe, Buckinghamshire; Ashford, Kent; Redditch, Worcestershire; Wigan, Greater Manchester; Scunthorpe, Lincolnshire; Bury, Greater Manchester; Bracknell, Berkshire; Burnley, Lancashire; Rugby, Warwickshire; Guildford, Surrey; Carlisle, Cumbria
```

#### Stage 2 - the next 100 (100 entries)

Chatham (76,955) down to Bexhill-on-Sea (43,755).

```
Chatham, Kent; Newcastle-under-Lyme, Staffordshire; Chesterfield, Derbyshire; Burton upon Trent, Staffordshire; Tamworth, Staffordshire; Shrewsbury, Shropshire; Woking, Surrey; St Albans, Hertfordshire; Harrogate, North Yorkshire; Crewe, Cheshire; South Shields, Tyne and Wear; Stafford, Staffordshire; Rotherham, South Yorkshire; Barnsley, South Yorkshire; Lowestoft, Suffolk; Walsall, West Midlands; Gosport, Hampshire; Dartford, Kent; Bognor Regis, West Sussex; Corby, Northamptonshire; Paignton, Devon; Maidenhead, Berkshire; Rochester, Kent; Ellesmere Port, Cheshire; Loughborough, Leicestershire; Dudley, West Midlands; Dewsbury, West Yorkshire; Mansfield, Nottinghamshire; Margate, Kent; Kettering, Northamptonshire; Cannock, Staffordshire; Sale, Greater Manchester; Taunton, Somerset; Runcorn, Cheshire; Farnborough, Hampshire; Tynemouth, Tyne and Wear; Hereford, Herefordshire; Halesowen, West Midlands; Widnes, Cheshire; Roby, Merseyside; Scarborough, North Yorkshire; Gravesend, Kent; Bebington, Merseyside; Kidderminster, Worcestershire; Stourbridge, West Midlands; Smethwick, West Midlands; Weymouth, Dorset; Brentwood, Essex; Barrow-in-Furness, Cumbria; Canterbury, Kent; Wellingborough, Northamptonshire; Sittingbourne, Kent; Macclesfield, Cheshire; Bootle, Merseyside; Carlton, Nottinghamshire; Clacton-on-Sea, Essex; Lancaster, Lancashire; Beeston, Nottinghamshire; Banbury, Oxfordshire; Torquay, Devon; Folkestone, Kent; Kingswinford, West Midlands; Bloxwich, West Midlands; Welwyn Garden City, Hertfordshire; Washington, Tyne and Wear; Royal Leamington Spa, Warwickshire; Tunbridge Wells, Kent; Hinckley, Leicestershire; Durham, County Durham; Wokingham, Berkshire; Crosby, Merseyside; Horsham, West Sussex; Yeovil, Somerset; Thundersley, Essex; Altrincham, Greater Manchester; Willenhall, West Midlands; Christchurch, Dorset; Keighley, West Yorkshire; Ashton-under-Lyne, Greater Manchester; Andover, Hampshire; Winchester, Hampshire; Eastleigh, Hampshire; Bridgwater, Somerset; Salisbury, Wiltshire; King's Lynn, Norfolk; Tipton, West Midlands; West Molesey, Surrey; Havant, Hampshire; Middleton, Greater Manchester; Kirkby, Merseyside; Leigh, Greater Manchester; Castleford, West Yorkshire; Wallsend, Tyne and Wear; Boston, Lincolnshire; Oldbury, West Midlands; Bletchley, Buckinghamshire; Grantham, Lincolnshire; Batley, West Yorkshire; Grays, Essex; Bexhill-on-Sea, East Sussex
```

#### Stage 3 - the next 150 (150 entries)

Trowbridge (43,750) down to Stroud (26,080).

```
Trowbridge, Wiltshire; Cheshunt, Hertfordshire; Worksop, Nottinghamshire; Braintree, Essex; Leighton Buzzard, Bedfordshire; Lytham St Anne's, Lancashire; Fareham, Hampshire; Newbury, Berkshire; Ramsgate, Kent; Urmston, Greater Manchester; Hatfield, Hertfordshire; Bury St Edmunds, Suffolk; Eccles, Greater Manchester; Bishop's Stortford, Hertfordshire; Hoddesdon, Hertfordshire; Bamber Bridge, Lancashire; Haywards Heath, West Sussex; Arnold, Nottinghamshire; Aldershot, Hampshire; Borehamwood, Hertfordshire; Blyth, Northumberland; Chorley, Lancashire; Leyland, Lancashire; Prescot, Merseyside; Rowley Regis, West Midlands; Ilkeston, Derbyshire; Canvey Island, Essex; Long Eaton, Derbyshire; Fleet, Hampshire; Bicester, Oxfordshire; Redcar, North Yorkshire; Chadderton, Greater Manchester; Whitley Bay, Tyne and Wear; Camberley, Surrey; Warwick, Warwickshire; West Bridgford, Nottinghamshire; Sutton in Ashfield, Nottinghamshire; Dover, Kent; Tonbridge, Kent; Chippenham, Wiltshire; Denton, Greater Manchester; Hyde, Greater Manchester; Epsom, Surrey; Hucknall, Nottinghamshire; Locks Heath, Hampshire; Exmouth, Devon; Hitchin, Hertfordshire; Skelmersdale, Lancashire; Accrington, Lancashire; Pudsey, West Yorkshire; Bridlington, East Riding of Yorkshire; Waterlooville, Hampshire; Bromsgrove, Worcestershire; Wigston, Leicestershire; Fulwood, Lancashire; Bilston, West Midlands; Didcot, Oxfordshire; Swadlincote, Derbyshire; Dunstable, Bedfordshire; Billericay, Essex; Horndean, Hampshire; Letchworth Garden City, Hertfordshire; Billingham, County Durham; Rustington, West Sussex; Nelson, Lancashire; Burgess Hill, West Sussex; Loughton, Essex; St Neots, Cambridgeshire; Great Malvern, Worcestershire; Abingdon-on-Thames, Oxfordshire; Brighouse, West Yorkshire; Caversham, Berkshire; New Haw, Surrey; Pontefract, West Yorkshire; Morecambe, Lancashire; Lichfield, Staffordshire; Morley, West Yorkshire; Winsford, Cheshire; Redhill, Surrey; Rayleigh, Essex; Brierley Hill, West Midlands; Whitstable, Kent; Sedgley, West Midlands; Chandler's Ford, Hampshire; Chichester, West Sussex; Rushden, Northamptonshire; Windsor, Berkshire; Prestwich, Greater Manchester; Barnstaple, Devon; Radcliffe, Greater Manchester; Bedworth, Warwickshire; Harpenden, Hertfordshire; Beverley, East Riding of Yorkshire; Spalding, Lincolnshire; Witney, Oxfordshire; Congleton, Cheshire; Northfleet, Kent; Consett, County Durham; Newark-on-Trent, Nottinghamshire; Heywood, Greater Manchester; Cleethorpes, Lincolnshire; Newton Abbot, Devon; Eston, North Yorkshire; Stanford-le-Hope, Essex; Jarrow, Tyne and Wear; Shipley, West Yorkshire; Heswall, Merseyside; Deal, Kent; Great Yarmouth, Norfolk; Kendal, Cumbria; Cramlington, Northumberland; Hertford, Hertfordshire; Farnworth, Greater Manchester; Bushey, Hertfordshire; Yate, Gloucestershire; Ashington, Northumberland; Evesham, Worcestershire; Stratford-upon-Avon, Warwickshire; Totton, Hampshire; Woodley, Berkshire; Stretford, Greater Manchester; Egham, Surrey; Lancing, West Sussex; Frome, Somerset; Burntwood, Staffordshire; Darwen, Lancashire; Daventry, Northamptonshire; Ormskirk, Lancashire; Atherton, Greater Manchester; Wickford, Essex; Ewell, Surrey; Melton Mowbray, Leicestershire; Witham, Essex; Tadworth, Surrey; Horley, Surrey; Walton-on-Thames, Surrey; Longbenton, Tyne and Wear; Stalybridge, Greater Manchester; Wisbech, Cambridgeshire; Haverhill, Suffolk; Sevenoaks, Kent; Droitwich Spa, Worcestershire; Ashton-in-Makerfield, Greater Manchester; Portishead, Somerset; East Grinstead, West Sussex; Rickmansworth, Hertfordshire; Fleetwood, Lancashire; Larkfield, Kent; Rugeley, Staffordshire; Stroud, Gloucestershire
```

#### Stage 4 - the last 106 towns (106 entries)

Little Hulton (25,825) down to Hazlemere (20,005).

```
Little Hulton, Greater Manchester; Broadstairs, Kent; Newton Aycliffe, County Durham; Wilmslow, Cheshire; Huntingdon, Cambridgeshire; Golborne, Greater Manchester; Thatcham, Berkshire; Thetford, Norfolk; Newport, Isle of Wight; Coseley, West Midlands; Bradley Stoke, Gloucestershire; Herne Bay, Kent; Cheadle Hulme, Greater Manchester; Bramley, South Yorkshire; Newton-le-Willows, Merseyside; Newquay, Cornwall; Hindley, Greater Manchester; Gorleston-on-Sea, Norfolk; St Austell, Cornwall; Lee-on-the-Solent, Hampshire; Ash, Surrey; Heanor, Derbyshire; Felixstowe, Suffolk; Market Harborough, Leicestershire; Ryde, Isle of Wight; Falmouth, Cornwall; Oadby, Leicestershire; Ascot, Berkshire; Sudbury, Suffolk; Droylsden, Greater Manchester; Seaford, East Sussex; Reigate, Surrey; Retford, Nottinghamshire; Chesham, Buckinghamshire; Rawtenstall, Lancashire; Shoreham-by-Sea, West Sussex; Chafford Hundred, Essex; Chester-le-Street, County Durham; Orrell, Greater Manchester; Potters Bar, Hertfordshire; Ingleby Barwick, North Yorkshire; Maldon, Essex; Bishop Auckland, County Durham; Thornaby-on-Tees, North Yorkshire; Hedge End, Hampshire; Truro, Cornwall; Royton, Greater Manchester; Whitehaven, Cumbria; Clifton, Nottinghamshire; Formby, Merseyside; Swinton, Greater Manchester; Ashford, Surrey; Kempston, Bedfordshire; Hailsham, East Sussex; South Ockendon, Essex; Kenilworth, Warwickshire; Reddish, Greater Manchester; Whitefield, Greater Manchester; Chapeltown, South Yorkshire; Crowborough, East Sussex; Coalville, Leicestershire; Normanton, West Yorkshire; Westhoughton, Greater Manchester; Biggleswade, Bedfordshire; Gainsborough, Lincolnshire; Ossett, West Yorkshire; Seaham, County Durham; Great Bookham, Surrey; Darlaston, West Midlands; Stowmarket, Suffolk; Sunbury-on-Thames, Surrey; Old Trafford, Greater Manchester; Dorchester, Dorset; Hebburn, Tyne and Wear; March, Cambridgeshire; Bingley, West Yorkshire; Staines-upon-Thames, Surrey; Workington, Cumbria; Kirkby-in-Ashfield, Nottinghamshire; Berkhamsted, Hertfordshire; Brownhills, West Midlands; Dronfield, Derbyshire; Dukinfield, Greater Manchester; Hardwicke, Gloucestershire; Clevedon, Somerset; Rothwell, West Yorkshire; Faversham, Kent; Dereham, Norfolk; Stamford, Lincolnshire; Horwich, Greater Manchester; Skegness, Lincolnshire; Stratton St Margaret, Wiltshire; Farnham, Surrey; Penwortham, Lancashire; Camborne, Cornwall; Spennymoor, County Durham; Maghull, Merseyside; Wednesbury, West Midlands; Stourport-on-Severn, Worcestershire; Harwich, Essex; Sandhurst, Berkshire; Ripley, Derbyshire; Goole, East Riding of Yorkshire; Hazel Grove, Greater Manchester; Buxton, Derbyshire; Hazlemere, Buckinghamshire
```

#### Stage 5 (optional) - the 32 London boroughs (32 entries)

Croydon down to Kensington and Chelsea.

```
Croydon, Greater London; Barnet, Greater London; Ealing, Greater London; Newham, Greater London; Brent, Greater London; Bromley, Greater London; Wandsworth, Greater London; Enfield, Greater London; Tower Hamlets, Greater London; Lambeth, Greater London; Southwark, Greater London; Redbridge, Greater London; Hillingdon, Greater London; Lewisham, Greater London; Greenwich, Greater London; Hounslow, Greater London; Waltham Forest, Greater London; Havering, Greater London; Haringey, Greater London; Hackney, Greater London; Harrow, Greater London; Bexley, Greater London; Islington, Greater London; Barking and Dagenham, Greater London; Camden, Greater London; Westminster, Greater London; Merton, Greater London; Sutton, Greater London; Richmond upon Thames, Greater London; Hammersmith and Fulham, Greater London; Kingston upon Thames, Greater London; Kensington and Chelsea, Greater London
```

## Entries I am not certain about

Flagged rather than quietly guessed. None of these break anything; they are places where the
official name and the name Google will match are not obviously the same, so the search may
return fewer plumbers than the town deserves.

| Entry | Why I am flagging it | What to do if it returns nothing |
|---|---|---|
| Thundersley, Essex | The official area is "Thundersley and South Benfleet". I kept the first half. | Try `South Benfleet, Essex`. |
| Kingswood, South Gloucestershire | The official area is "Kingswood and Fishponds" and straddles two counties (Gloucestershire and Bristol). | Try `Kingswood, Bristol`. |
| Sutton Coldfield, West Midlands | The official name is "Royal Sutton Coldfield"; I used the everyday name. | Unlikely to matter. |
| Tunbridge Wells, Kent | Official name "Royal Tunbridge Wells". The live config already uses this shorter form. | Unlikely to matter. |
| Loughborough, Leicestershire | The source table prints the county as "Leicester", which is the city, not the county. I corrected it. | Unlikely to matter. |
| Kingston upon Hull, East Riding of Yorkshire | Correct ceremonially, but almost nobody writes it that way; locals say "Hull". | Try `Hull, East Yorkshire`. |
| Old Trafford, Greater Manchester | A district of Manchester, not a town. It is in the official list, but its plumbers are Manchester plumbers. | Drop it if Manchester is already covered. |
| Reading, Bath, Bury, Sale, Leigh, Hyde, Deal, Ash, March, Crosby, Clifton, Bramley, Normanton, Eston, Stretford | The town name is also an ordinary English word or a very common place name. The county on the end is what keeps the search honest, so never drop it. | Check the first few results really are in that county. |
| Gosport, Hampshire and Redditch, Worcestershire | Wikipedia had no map coordinate for these two, so I typed their positions in by hand. This only affects the distance column above, not the search. | Nothing. |

## One more thing: neighbouring entries overlap

158 of the 456 non-London entries sit within 4 miles of a larger entry in the same
list - Gateshead is 1.4 miles from Newcastle, Birkenhead 1.3 from Liverpool, Salford 2.0 from
Manchester, Chatham 1.7 from Gillingham. A plumber in one will usually show up in the search
for the other.

This is not a mistake in the list: they are genuinely separate built-up areas and a local
plumber really does trade in one of them. It matters for two practical reasons:

- The pipeline will not create a duplicate lead (`_already_in_db` checks the Google place id
  and the name+town pair), so you will not email the same plumber twice.
- But each overlapping search still costs a Google Places request, and after a while it returns
  nothing new. That is a cost problem, not a spam problem, and the rollout plan covers it.

If money gets tight, the cheapest trim is to drop entries within 3 miles of a bigger one
(107 entries) before dropping anything else.

## Sources

- ONS built-up areas and their populations and counties: https://en.wikipedia.org/wiki/List_of_ONS_built-up_areas_in_England_by_population (read 2026-09-20), which tabulates the ONS release at https://www.ons.gov.uk/peoplepopulationandcommunity/housing/articles/townsandcitiescharacteristicsofbuiltupareasenglandandwales/census2021
- London borough populations (2022 estimates) and coordinates: https://en.wikipedia.org/wiki/List_of_London_boroughs (read 2026-09-20)
- Town coordinates for the Oxted distance check: Wikipedia GeoData API (read 2026-09-20)
- The home exclusion zone: `pipeline/config.py`, `SOURCING_EXCLUDE_CENTER` and `SOURCING_EXCLUDE_RADIUS_MILES`

