"""Dated, fictional operator workspaces. Only Solara has runnable quote fixtures."""
from __future__ import annotations

from copy import deepcopy

DEFAULT_COMPANY_ID = 'solara-cables'
AS_OF = '2026-10-14'
COPPER_FIXTURES = ['late-copper', 'express-copper', 'eu-copper']


def _material(material_id, name, unit, stock, daily, monthly, criticality, *keywords):
    return {'material_id': material_id, 'name': name, 'quantity_unit': unit,
            'stock': stock, 'consumption_per_day': daily,
            'monthly_requirement': monthly, 'criticality': criticality,
            'keywords': list(keywords) or [name.lower()]}


def _profile(company_id, name, sector, size, locations, currency, materials,
             suppliers, orders, routes, *, description, keywords=(), coverage=False,
             defaults=None):
    material_map = {item['material_id']: item for item in materials}
    supplier_rows = [
        {'supplier_id': f'{company_id}-supplier-{index}', 'name': supplier_name,
         'country': country, 'settlement_currency': settlement,
         'materials': material_ids, 'lead_time_days': lead}
        for index, (supplier_name, country, settlement, material_ids, lead) in enumerate(suppliers, 1)
    ]
    order_rows = [
        {'order_id': f'{company_id}-order-{index}', 'material_id': material_id,
         'quantity': quantity, 'unit': material_map[material_id]['quantity_unit'],
         'need_by': need_by, 'status': status}
        for index, (material_id, quantity, need_by, status) in enumerate(orders, 1)
    ]
    route_rows = [
        {'route_id': f'{company_id}-route-{index}', 'origin': origin,
         'destination': destination, 'via': via, 'materials': material_ids,
         'direction': direction, 'currency': route_currency}
        for index, (origin, destination, via, material_ids, direction, route_currency)
        in enumerate(routes, 1)
    ]
    currencies = list(dict.fromkeys([currency] + [s[2] for s in suppliers] + [r[5] for r in routes]))
    return {'company_id': company_id, 'name': name, 'sector': sector, 'size': size,
            'synthetic': True, 'as_of': AS_OF, 'description': description,
            'locations': [{'country': country, 'city': city} for country, city in locations],
            'reporting_currency': currency, 'materials': materials, 'suppliers': supplier_rows,
            'orders': order_rows, 'routes': route_rows, 'currencies': currencies,
            'news_keywords': list(dict.fromkeys([sector.lower(), *keywords, *[word for m in materials for word in m['keywords']]])),
            'quote_coverage': {'materials': ['copper'] if coverage else [],
                               'fixture_ids': COPPER_FIXTURES[:] if coverage else [],
                               'note': ('Three synthetic copper fixtures support the kg-only comparison.'
                                        if coverage else 'No runnable quote fixtures cover this fictional company; inventory and news exploration only.')},
            'buyer_defaults': defaults}


_PROFILES = [
    _profile('solara-cables', 'Solara Cables', 'Electrical cable manufacturing', 'Mid-market',
        [('India', 'Pune'), ('India', 'Chennai')], 'USD',
        [_material('copper', 'Copper cathode', 'kg', '2000', '333.33', '10000', 'high', 'copper', 'cathode'),
         _material('polymer', 'Cable insulation polymer', 'kg', '5800', '125', '3750', 'medium', 'polymer', 'PVC')],
        [('Atacama Red Metals SpA [SYNTHETIC]', 'Chile', 'USD', ['copper'], 45),
         ('Swift Copper Trading Ltd [SYNTHETIC]', 'India', 'USD', ['copper', 'polymer'], 14),
         ('Europa Copper SA [SYNTHETIC]', 'Germany', 'EUR', ['copper'], 10)],
        [('copper', '10000', '2026-11-10', 'open'), ('polymer', '3750', '2026-11-20', 'planned')],
        [('Valparaiso, Chile', 'Pune, India', ['Pacific Ocean', 'Mumbai'], ['copper'], 'import', 'USD'),
         ('Hamburg, Germany', 'Chennai, India', ['Suez Canal'], ['copper'], 'import', 'EUR')],
        description='Fictional cable maker with an illustrative copper procurement workflow.',
        keywords=('cable', 'India', 'Chile', 'Germany'), coverage=True,
        defaults={'offer_ids': [], 'expected_versions': {}, 'material': 'copper',
                  'quantity_kg': '10000', 'current_stock_kg': '2000',
                  'need_by': '2026-11-05', 'decision_date': AS_OF,
                  'reporting_currency': 'USD', 'budget': '100000',
                  'additional_cost_per_kg': '0.25'}),
    _profile('terrapack', 'TerraPack Films', 'Flexible packaging', 'Regional',
        [('India', 'Ahmedabad')], 'INR',
        [_material('pet-resin', 'PET resin', 'kg', '18500', '950', '28500', 'high', 'PET', 'resin'),
         _material('inks', 'Packaging inks', 'litre', '2100', '48', '1440', 'medium', 'packaging', 'inks'),
         _material('aluminium-film', 'Aluminium film', 'roll', '145', '6', '180', 'high', 'aluminium', 'film')],
        [('ResinArc', 'Singapore', 'USD', ['pet-resin'], 21), ('ColorMill', 'India', 'INR', ['inks'], 6),
         ('FoilHarbor', 'UAE', 'AED', ['aluminium-film'], 16)],
        [('pet-resin', '28000', '2026-11-12', 'open'), ('aluminium-film', '160', '2026-11-04', 'planned')],
        [('Singapore', 'Ahmedabad, India', ['Nhava Sheva'], ['pet-resin'], 'import', 'USD'),
         ('Ahmedabad, India', 'Dubai, UAE', ['Mundra'], ['aluminium-film'], 'export', 'AED')],
        description='Fictional packaging-film producer exposed to resin and foil routes.', keywords=('packaging', 'petrochemical')),
    _profile('loomworks', 'LoomWorks Textiles', 'Textile production', 'Small',
        [('Bangladesh', 'Dhaka'), ('Bangladesh', 'Chattogram')], 'BDT',
        [_material('cotton', 'Cotton fibre', 'bale', '920', '32', '960', 'high', 'cotton', 'fibre'),
         _material('dyes', 'Reactive dyes', 'kg', '4400', '110', '3300', 'medium', 'textile', 'dyes')],
        [('Delta Cotton', 'India', 'USD', ['cotton'], 11), ('Chromex', 'China', 'CNY', ['dyes'], 24)],
        [('cotton', '900', '2026-11-02', 'open')],
        [('Kolkata, India', 'Dhaka, Bangladesh', ['Benapole'], ['cotton'], 'import', 'USD'),
         ('Shanghai, China', 'Chattogram, Bangladesh', ['Singapore'], ['dyes'], 'import', 'CNY')],
        description='Fictional fabric mill balancing fibre and colourant replenishment.', keywords=('textile', 'Bangladesh')),
    _profile('harvest-foods', 'Harvest Foods', 'Food processing', 'Mid-market',
        [('Kenya', 'Nairobi'), ('Kenya', 'Mombasa')], 'KES',
        [_material('wheat', 'Wheat grain', 'tonne', '1800', '74', '2220', 'high', 'wheat', 'grain'),
         _material('oil', 'Sunflower oil', 'litre', '36000', '850', '25500', 'medium', 'sunflower', 'edible oil'),
         _material('cartons', 'Food cartons', 'unit', '98000', '2700', '81000', 'low', 'carton', 'packaging'),
         _material('sugar', 'Refined sugar', 'tonne', '420', '15', '450', 'medium', 'sugar')],
        [('Prairie Grain', 'Canada', 'USD', ['wheat'], 38), ('SunCoast Oils', 'Tanzania', 'TZS', ['oil'], 12),
         ('PackEast', 'Kenya', 'KES', ['cartons'], 5),
         ('Kampala Cane Co [SYNTHETIC]', 'Uganda', 'USD', ['sugar'], 17)],
        [('wheat', '2100', '2026-12-01', 'open'), ('oil', '24000', '2026-11-08', 'planned'),
         ('sugar', '450', '2026-11-26', 'planned')],
        [('Vancouver, Canada', 'Mombasa, Kenya', ['Indian Ocean'], ['wheat'], 'import', 'USD'),
         ('Nairobi, Kenya', 'Kampala, Uganda', ['Malaba'], ['cartons'], 'export', 'KES'),
         ('Kampala, Uganda', 'Nairobi, Kenya', ['Malaba'], ['sugar'], 'import', 'USD')],
        description='Fictional food processor with grain and edible-oil exposures.', keywords=('food', 'East Africa')),
    _profile('meridian-pharma', 'Meridian Pharma', 'Pharmaceutical manufacturing', 'Enterprise',
        [('Switzerland', 'Basel'), ('Ireland', 'Cork')], 'CHF',
        [_material('api', 'Active ingredient', 'kg', '1250', '34', '1020', 'high', 'pharmaceutical', 'API'),
         _material('vials', 'Sterile vials', 'unit', '120000', '3800', '114000', 'high', 'glass', 'vials'),
         _material('ethanol', 'Medical ethanol', 'litre', '9000', '220', '6600', 'medium', 'ethanol')],
        [('BioMolecule', 'India', 'USD', ['api'], 31), ('ClearGlass', 'Germany', 'EUR', ['vials'], 17),
         ('PureSolvent', 'Ireland', 'EUR', ['ethanol'], 8)],
        [('api', '1000', '2026-11-25', 'open'), ('vials', '100000', '2026-11-15', 'open')],
        [('Hyderabad, India', 'Basel, Switzerland', ['Frankfurt'], ['api'], 'import', 'USD'),
         ('Basel, Switzerland', 'Cork, Ireland', ['Dublin'], ['vials'], 'export', 'CHF')],
        description='Fictional pharmaceutical manufacturer with ingredient and sterile-packaging needs.', keywords=('healthcare', 'Europe')),
    _profile('forgeline-components', 'ForgeLine Components', 'Automotive components', 'Mid-market',
        [('Mexico', 'Monterrey')], 'MXN',
        [_material('steel', 'Cold-rolled steel', 'tonne', '610', '24', '720', 'high', 'steel'),
         _material('copper', 'Copper rod', 'kg', '7800', '260', '7800', 'medium', 'copper'),
         _material('bearings', 'Precision bearings', 'unit', '14000', '430', '12900', 'high', 'bearing')],
        [('LakeSteel', 'USA', 'USD', ['steel'], 13), ('Andes Wire', 'Chile', 'USD', ['copper'], 26),
         ('Axis Parts', 'Japan', 'JPY', ['bearings'], 29)],
        [('steel', '700', '2026-11-18', 'open'), ('bearings', '12000', '2026-11-28', 'planned')],
        [('Detroit, USA', 'Monterrey, Mexico', ['Laredo'], ['steel'], 'import', 'USD'),
         ('Monterrey, Mexico', 'Texas, USA', ['Laredo'], ['bearings'], 'export', 'MXN')],
        description='Fictional component maker; its copper stock is not covered by the demo quotes.', keywords=('automotive', 'Mexico')),
    _profile('gridspring-batteries', 'GridSpring Batteries', 'Battery assembly', 'Growth',
        [('South Korea', 'Busan'), ('Vietnam', 'Hai Phong')], 'KRW',
        [_material('lithium', 'Lithium carbonate', 'tonne', '88', '4', '120', 'high', 'lithium'),
         _material('nickel', 'Nickel sulfate', 'tonne', '175', '7', '210', 'high', 'nickel'),
         _material('separators', 'Cell separators', 'roll', '2400', '90', '2700', 'medium', 'battery', 'separator'),
         _material('graphite', 'Battery graphite', 'tonne', '130', '5', '150', 'high', 'graphite')],
        [('Altiplano Lithium', 'Chile', 'USD', ['lithium'], 42), ('Pacific Nickel', 'Indonesia', 'USD', ['nickel'], 25),
         ('CellTex', 'Japan', 'JPY', ['separators'], 18),
         ('Qingdao Graphite Supply [SYNTHETIC]', 'China', 'CNY', ['graphite'], 27)],
        [('lithium', '110', '2026-12-10', 'open'), ('separators', '2500', '2026-11-20', 'planned'),
         ('graphite', '150', '2026-11-25', 'planned')],
        [('Antofagasta, Chile', 'Busan, South Korea', ['Pacific Ocean'], ['lithium'], 'import', 'USD'),
         ('Busan, South Korea', 'Hai Phong, Vietnam', ['East China Sea'], ['separators'], 'export', 'KRW'),
         ('Qingdao, China', 'Busan, South Korea', ['Yellow Sea'], ['graphite'], 'import', 'CNY')],
        description='Fictional battery assembler exposed to mineral and separator supply.', keywords=('battery', 'energy storage')),
    _profile('circuitpeak-systems', 'CircuitPeak Systems', 'Electronics assembly', 'Small',
        [('Malaysia', 'Penang')], 'MYR',
        [_material('chips', 'Power semiconductors', 'unit', '74000', '2100', '63000', 'high', 'semiconductor', 'chip'),
         _material('boards', 'Printed circuit boards', 'unit', '42000', '1200', '36000', 'high', 'PCB', 'electronics')],
        [('SiliconGate', 'Taiwan', 'USD', ['chips'], 35), ('BoardCraft', 'China', 'CNY', ['boards'], 19)],
        [('chips', '65000', '2026-12-05', 'open')],
        [('Kaohsiung, Taiwan', 'Penang, Malaysia', ['South China Sea'], ['chips'], 'import', 'USD'),
         ('Penang, Malaysia', 'Singapore', ['Malacca Strait'], ['boards'], 'export', 'MYR')],
        description='Fictional electronics assembler tracking chips and board stock.', keywords=('electronics', 'Taiwan')),
    _profile('atlas-petrochem', 'Atlas Petrochem', 'Petrochemical blending', 'Enterprise',
        [('UAE', 'Abu Dhabi'), ('Oman', 'Sohar')], 'AED',
        [_material('naphtha', 'Naphtha feedstock', 'barrel', '190000', '7100', '213000', 'high', 'naphtha', 'crude oil'),
         _material('methanol', 'Methanol', 'tonne', '3900', '135', '4050', 'medium', 'methanol'),
         _material('catalyst', 'Process catalyst', 'kg', '5400', '70', '2100', 'medium', 'catalyst')],
        [('Gulf Feedstock', 'Saudi Arabia', 'USD', ['naphtha'], 9), ('Oman Chemicals', 'Oman', 'OMR', ['methanol'], 7),
         ('CatalystWorks', 'Germany', 'EUR', ['catalyst'], 32)],
        [('naphtha', '205000', '2026-11-12', 'open'), ('methanol', '4000', '2026-11-30', 'planned')],
        [('Dammam, Saudi Arabia', 'Abu Dhabi, UAE', ['Persian Gulf'], ['naphtha'], 'import', 'USD'),
         ('Sohar, Oman', 'Mumbai, India', ['Arabian Sea'], ['methanol'], 'export', 'OMR')],
        description='Fictional petrochemical blender with Gulf feedstock routes.', keywords=('oil', 'Gulf')),
    _profile('stonebridge-materials', 'StoneBridge Materials', 'Construction materials', 'Regional',
        [('South Africa', 'Durban'), ('South Africa', 'Johannesburg')], 'ZAR',
        [_material('clinker', 'Cement clinker', 'tonne', '12500', '480', '14400', 'high', 'cement', 'clinker'),
         _material('gypsum', 'Gypsum', 'tonne', '3100', '90', '2700', 'medium', 'gypsum'),
         _material('rebar', 'Reinforcing bar', 'tonne', '2200', '75', '2250', 'medium', 'steel', 'rebar'),
         _material('aggregate', 'Crushed aggregate', 'tonne', '17800', '620', '18600', 'low', 'aggregate')],
        [('KilnSouth', 'South Africa', 'ZAR', ['clinker'], 6), ('RedSea Gypsum', 'Egypt', 'USD', ['gypsum'], 34),
         ('SteelCape', 'South Africa', 'ZAR', ['rebar'], 10),
         ('Maputo Quarry Supply [SYNTHETIC]', 'Mozambique', 'USD', ['aggregate'], 12)],
        [('clinker', '14000', '2026-11-14', 'open'), ('gypsum', '2600', '2026-12-01', 'planned'),
         ('aggregate', '18600', '2026-11-18', 'planned')],
        [('Alexandria, Egypt', 'Durban, South Africa', ['Suez Canal'], ['gypsum'], 'import', 'USD'),
         ('Durban, South Africa', 'Maputo, Mozambique', ['N4 corridor'], ['clinker'], 'export', 'ZAR'),
         ('Maputo, Mozambique', 'Durban, South Africa', ['Maputo Port', 'Indian Ocean'], ['aggregate'], 'import', 'USD')],
        description='Fictional construction-material supplier balancing kiln and mineral inputs.', keywords=('construction', 'cement')),
]


def _add_catalogue_lines(company_id, lines):
    """Each added stock line has its own fictional source, order, and route."""
    profile = next(item for item in _PROFILES if item['company_id'] == company_id)
    destination = ', '.join((profile['locations'][0]['city'], profile['locations'][0]['country']))
    for row in lines:
        (material_id, name, unit, stock, daily, monthly, criticality,
         source_city, source_country, currency, lead, keywords) = row
        profile['materials'].append(_material(material_id, name, unit, stock, daily,
                                              monthly, criticality, *keywords))
        profile['suppliers'].append({
            'supplier_id': f'{company_id}-supplier-{len(profile["suppliers"]) + 1}',
            'name': f'{name} Source [SYNTHETIC]', 'country': source_country,
            'settlement_currency': currency, 'materials': [material_id],
            'lead_time_days': lead})
        profile['orders'].append({
            'order_id': f'{company_id}-order-{len(profile["orders"]) + 1}',
            'material_id': material_id, 'quantity': monthly, 'unit': unit,
            'need_by': '2026-12-15', 'status': 'planned'})
        profile['routes'].append({
            'route_id': f'{company_id}-route-{len(profile["routes"]) + 1}',
            'origin': f'{source_city}, {source_country}', 'destination': destination,
            'via': [], 'materials': [material_id], 'direction': 'import',
            'currency': currency})
        if currency not in profile['currencies']:
            profile['currencies'].append(currency)
        profile['news_keywords'].extend(word for word in keywords if word not in profile['news_keywords'])


_add_catalogue_lines('solara-cables', [
    ('aluminium', 'Aluminium conductor', 'kg', '4300', '145', '4350', 'medium', 'Dubai', 'UAE', 'AED', 24, ('aluminium', 'conductor')),
])
_add_catalogue_lines('loomworks', [
    ('yarn', 'Spun yarn', 'kg', '7100', '230', '6900', 'high', 'Surat', 'India', 'USD', 17, ('yarn', 'textile')),
])
_add_catalogue_lines('circuitpeak-systems', [
    ('solder', 'Lead-free solder paste', 'kg', '860', '24', '720', 'medium', 'Osaka', 'Japan', 'JPY', 22, ('solder', 'electronics')),
])
_add_catalogue_lines('gridspring-batteries', [
    ('cobalt', 'Cobalt sulfate', 'tonne', '94', '3', '90', 'high', 'Lubumbashi', 'DR Congo', 'USD', 43, ('cobalt', 'battery')),
    ('manganese', 'Manganese sulfate', 'tonne', '118', '4', '120', 'medium', 'Port Elizabeth', 'South Africa', 'USD', 37, ('manganese', 'battery')),
    ('electrolyte', 'Battery electrolyte', 'litre', '28000', '980', '29400', 'high', 'Shanghai', 'China', 'CNY', 23, ('electrolyte', 'battery')),
    ('aluminium-foil', 'Cathode aluminium foil', 'roll', '1750', '63', '1890', 'medium', 'Tokyo', 'Japan', 'JPY', 19, ('aluminium', 'battery')),
    ('copper-foil', 'Anode copper foil', 'roll', '1620', '58', '1740', 'medium', 'Kaohsiung', 'Taiwan', 'USD', 21, ('copper', 'battery')),
    ('cell-cans', 'Cell cans', 'unit', '132000', '4800', '144000', 'high', 'Suzhou', 'China', 'CNY', 20, ('cell', 'battery')),
])
_add_catalogue_lines('atlas-petrochem', [
    ('ethane', 'Ethane feedstock', 'tonne', '14500', '510', '15300', 'high', 'Doha', 'Qatar', 'USD', 12, ('ethane', 'gas')),
    ('propane', 'Propane feedstock', 'tonne', '9200', '340', '10200', 'medium', 'Dammam', 'Saudi Arabia', 'USD', 11, ('propane', 'LPG')),
    ('benzene', 'Benzene', 'tonne', '4100', '132', '3960', 'medium', 'Ulsan', 'South Korea', 'USD', 31, ('benzene', 'aromatics')),
    ('toluene', 'Toluene', 'tonne', '3650', '117', '3510', 'medium', 'Singapore', 'Singapore', 'SGD', 18, ('toluene', 'aromatics')),
    ('ethylene', 'Ethylene', 'tonne', '7700', '265', '7950', 'high', 'Jubail', 'Saudi Arabia', 'USD', 14, ('ethylene', 'olefins')),
    ('caustic-soda', 'Caustic soda', 'tonne', '2350', '82', '2460', 'medium', 'Mumbai', 'India', 'INR', 20, ('caustic soda', 'chemical')),
    ('sulfur', 'Elemental sulfur', 'tonne', '6950', '245', '7350', 'medium', 'Ras Laffan', 'Qatar', 'USD', 15, ('sulfur', 'refinery')),
    ('hydrogen', 'Compressed hydrogen', 'cylinder', '1840', '57', '1710', 'high', 'Sohar', 'Oman', 'OMR', 9, ('hydrogen', 'gas')),
    ('polymer-additive', 'Polymer stabilizer', 'kg', '6600', '195', '5850', 'low', 'Antwerp', 'Belgium', 'EUR', 33, ('polymer', 'stabilizer')),
])
_add_catalogue_lines('stonebridge-materials', [
    ('limestone', 'Limestone', 'tonne', '25400', '840', '25200', 'high', 'Gaborone', 'Botswana', 'BWP', 13, ('limestone', 'cement')),
    ('fly-ash', 'Fly ash', 'tonne', '8800', '295', '8850', 'medium', 'Maputo', 'Mozambique', 'USD', 15, ('fly ash', 'cement')),
    ('silica-sand', 'Silica sand', 'tonne', '7600', '255', '7650', 'medium', 'Windhoek', 'Namibia', 'NAD', 17, ('silica', 'sand')),
    ('admixture', 'Concrete admixture', 'litre', '18500', '620', '18600', 'medium', 'Rotterdam', 'Netherlands', 'EUR', 29, ('concrete', 'admixture')),
])

_BY_ID = {profile['company_id']: profile for profile in _PROFILES}


def list_companies():
    return deepcopy(_PROFILES)


def get_company(company_id):
    profile = _BY_ID.get(company_id)
    return deepcopy(profile) if profile is not None else None
