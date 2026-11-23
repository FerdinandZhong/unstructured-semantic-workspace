# Ossie review sheet: adventureworks_manufacturing_demo

Generated from the live model. Edit the overlay file, not this sheet.

## Subject areas

| Subject area | Description | Datasets |
| --- | --- | --- |
| Sales orders and customers | What customers ordered, when it was due and shipped, and what it was worth. Covers sales orders, their lines, customer accounts and reseller stores. | Sales_SalesOrderHeader, Sales_SalesOrderDetail, Sales_Customer, Sales_Store |
| Purchasing and suppliers | What was ordered from suppliers, what was received, rejected and stocked, and the supply terms recorded per supplier and product. Covers vendors, vendor-product terms, purchase orders, their lines and shipping methods. | Purchasing_Vendor, Purchasing_ProductVendor, Purchasing_PurchaseOrderHeader, Purchasing_PurchaseOrderDetail, Purchasing_ShipMethod |
| Products and bills of materials | The product master: items sold or used in manufacturing, their categories, models, units of measure and the components that make up each assembly. | Production_Product, Production_ProductCategory, Production_ProductSubcategory, Production_ProductModel, Production_UnitMeasure, Production_BillOfMaterials |
| Production and quality | Work orders, their routing operations by work centre, planned and actual dates and costs, and scrap with its reason. | Production_WorkOrder, Production_WorkOrderRouting, Production_ScrapReason, Production_Location |
| Inventory | Quantity held per product and location at the source snapshot, with shelf and bin. | Production_ProductInventory, Production_Location |
| Product documents | Maintenance, service and assembly documents and the products each document applies to. | Production_Document, Production_ProductDocument |
| Parties, people and addresses | Shared party keys, contact persons and postal addresses used by customers, stores and vendors. | Person_BusinessEntity, Person_Person, Person_Address |

## Concepts

| Concept | Definition | Synonyms | Broader |
| --- | --- | --- | --- |
| Customer account identifier (`customer_identifier`) | A customer account key interpreted within the source system. | customer ID, customer number, account number, customer account |  |
| Product identifier (`product_identifier`) | A product key interpreted within its source namespace. | product ID, product number, SKU, part number, item number, item code |  |
| Reported inventory quantity (`inventory_position`) | A source reports quantity at a product/location snapshot. | stock on hand, on-hand quantity, inventory level, stock quantity |  |
| Reported production scrap (`scrap_quality_context`) | A source reports scrapped quantity for a product/work order. | scrap, scrapped quantity, production rejects, waste |  |
| Supplier identifier (`supplier_identifier`) | An AdventureWorks vendor business-entity key in its declared source namespace. | vendor ID, supplier ID, vendor number, supplier number, supplier code |  |
| Promised delivery date (`delivery_promise`) | A date promised by a contract or amendment for delivery of a scoped order. | promised delivery date, committed delivery date, agreed delivery date, delivery commitment |  |
| Sales order (`sales_order`) | A confirmed sales commitment with a due date and customer assignment. | SO, customer order, order, sales order number |  |
| Sales line revenue (`revenue`) | Total monetary value of goods sold on sales-order lines. | sales value, sales amount, line total, order value, turnover | sales_order |
| Unit price (`unit_price`) | Monetary value charged per unit on a sales-order line before discount. | selling price, price per unit, sales price |  |
| Production scrap rate (`production_scrap`) | Ratio of scrapped to requested units across work orders, indicating production quality. | scrap rate, scrap percentage, reject rate, scrap ratio |  |
| Delivery lateness (`delivery_lateness`) | Whether a sales order shipped after its recorded due date. Shipment is not delivery, so the source does not show when the customer received the goods. | late shipment, late dispatch, shipped after due date, overdue shipment |  |
| Work order status (`work_order_status`) | Whether a production work order has a recorded completion date. | open work orders, work in progress, WIP orders, unfinished work orders |  |
| Production requested units (`production_volume`) | Total units requested on work orders for a product and period. | production quantity, quantity to build, planned production quantity, build quantity |  |
| Product (`product`) | An item that the company sells or uses to manufacture what it sells, named as on the product master. | item, part, material, article, goods |  |
| Supplier (`supplier`) | A company from which parts or other goods are purchased. The source calls it a vendor. | vendor, seller, supplier name, vendor name |  |
| Reseller store (`reseller_store`) | A business customer that resells the company's products. The source calls it a store. | reseller, dealer, retailer, store, business customer, buyer |  |
| Contact person (`contact_person`) | A person known to the company as an employee, an individual customer, or a store or vendor contact. | contact, individual, person, representative |  |
| Postal address (`postal_address`) | A street address used as the bill-to or ship-to address of a sales order. | address, ship-to address, bill-to address, delivery address, billing address |  |
| Sales territory (`sales_territory`) | The sales region a customer or a sale is assigned to. Only the territory key is in this pack. | sales region, territory, region |  |
| Sales ordered quantity (`ordered_quantity`) | Number of product units a customer ordered on a sales-order line. It is an order fact, not a contractual minimum and not proof of delivery. | quantity ordered, order quantity, units ordered, order qty | sales_order |
| Order date (`order_date`) | The date an order was created, for a sales order or a purchase order. It is not a due, ship or receipt date. | date ordered, order placement date, PO date, booking date |  |
| Shipment date (`shipment_date`) | The date a sales order was shipped to the customer. Shipment does not prove delivery or acceptance. | ship date, dispatch date, despatch date, shipped date | sales_order |
| Sales order status (`sales_order_status`) | The current processing state of a sales order, from in process to shipped or cancelled. | order status, order state, fulfilment status | sales_order |
| Sales channel (`sales_channel`) | Whether a sales order was placed online by the customer or entered by a sales person. | order channel, online order, web order, direct sales | sales_order |
| Customer purchase order reference (`customer_po_reference`) | The customer's own purchase order number quoted on a sales order. | customer PO, customer PO number, buyer's order number, customer reference | sales_order |
| Line discount (`line_discount`) | The discount applied to a sales-order line. The source comment calls it a discount amount; the data holds values between 0 and 0.40. | discount, price reduction |  |
| Order total due (`order_total`) | The total amount due on an order, computed as subtotal plus tax plus freight. | total due, order total, amount due, grand total, order value |  |
| Tax amount (`tax_amount`) | The tax charged on an order, held at order level. | tax, sales tax, VAT, GST | order_total |
| Freight charge (`freight_charge`) | The shipping cost charged on an order, held at order level. | freight, shipping cost, delivery charge, carriage, transport cost | order_total |
| Shipping method (`ship_method`) | The shipping company or service used to move an order. | carrier, shipping company, freight carrier, delivery service, mode of transport |  |
| Carrier tracking number (`tracking_number`) | The shipment tracking number supplied by the shipper for a sales-order line. | tracking number, waybill number, consignment number, shipment reference |  |
| List price (`list_price`) | The catalogue selling price of one unit of a product before any discount. The price charged is the unit price on the order line. | catalogue price, catalog price, price list price, base price |  |
| Standard cost (`standard_cost`) | The standard internal cost of one unit of a product. It is not a selling price or a vendor price. | std cost, standard unit cost, cost price |  |
| Make or buy (`make_or_buy`) | Whether a product is manufactured in-house or purchased. | make/buy, manufactured in-house, purchased part, bought-in part | product |
| Days to manufacture (`manufacturing_lead_time`) | The number of days required to manufacture a product, as held on the product master. | manufacturing lead time, production lead time, build time | product |
| Safety stock level (`safety_stock`) | The minimum inventory quantity that should be kept for a product. | safety stock, buffer stock, minimum stock level |  |
| Reorder point (`reorder_point`) | The inventory level that triggers a purchase order or a work order for a product. | ROP, reorder level, replenishment point, order point |  |
| Product category (`product_category`) | The top-level grouping of products, such as bikes or components. | category, product group, product family |  |
| Product subcategory (`product_subcategory`) | The second-level grouping of products inside a product category. | subcategory, product type, sub-group | product_category |
| Product model (`product_model`) | The model a product variant belongs to. Variants of one model differ by attributes such as size and color. | model, model name, product family |  |
| Product line (`product_line`) | The product line code of a product. Road, mountain, touring or standard. | line, range, product range |  |
| Unit of measure (`unit_of_measure`) | The unit in which a quantity, size or weight is expressed, held as a three-character code. | UoM, UOM, unit, unit code, measurement unit |  |
| Bill of materials (`bill_of_materials`) | The list of components needed to make an assembly, with the parent product, the component and the effective dates. | BOM, product structure, parts list, components list |  |
| Component quantity per assembly (`component_quantity`) | The quantity of one component needed to create one unit of the parent assembly. | quantity per, usage quantity, qty per assembly | bill_of_materials |
| Purchase order (`purchase_order`) | An order placed with a vendor for one or more products. | PO, purchase order number, supplier order, buy order |  |
| Purchase order status (`purchase_order_status`) | The current state of a purchase order. Pending, approved, rejected or complete. | PO status, order status, approval status | purchase_order |
| Purchase ordered quantity (`purchase_ordered_quantity`) | The quantity of a product ordered from a vendor on a purchase-order line. It is what was asked for, not what arrived. | PO quantity, quantity ordered, ordered quantity, order qty | purchase_order |
| Received quantity (`received_quantity`) | The quantity actually received from the vendor on a purchase-order line, before inspection. | goods received, quantity received, delivered quantity, receipt quantity | purchase_order |
| Rejected quantity (`rejected_quantity`) | The part of the received quantity that was rejected during inspection. | rejects, inspection rejects, quantity rejected, non-conforming quantity | received_quantity |
| Stocked quantity (`stocked_quantity`) | The quantity accepted into inventory from a purchase-order line. Received quantity minus rejected quantity. | accepted quantity, quantity accepted into stock, quantity put away | received_quantity |
| Expected receipt date (`expected_receipt_date`) | The date a purchased product is expected to be received, as recorded on the purchase-order line. | expected delivery date, PO due date, need-by date, required-by date | purchase_order |
| Supplier unit price (`supplier_unit_price`) | The vendor's selling price for one unit of a product, on a purchase-order line or as the vendor's usual price. | purchase price, vendor price, supplier price, unit cost |  |
| Average supplier lead time (`supplier_lead_time`) | The average number of days between placing an order with a vendor and receiving the product. It is a recorded average, not a promise. | lead time, vendor lead time, average lead time, delivery lead time |  |
| Minimum order quantity (`minimum_order_quantity`) | The smallest quantity that should be ordered from a vendor for a product. The source comment conflicts with the column name; domain review is pending. | MOQ, minimum order, min order qty, minimum order size |  |
| Maximum order quantity (`maximum_order_quantity`) | The largest quantity that should be ordered from a vendor for a product. The source comment conflicts with the column name; domain review is pending. | maximum order, max order qty, order ceiling, order cap |  |
| Quantity on order (`on_order_quantity`) | The quantity of a product currently on order with a vendor. | on order, open order quantity, outstanding order quantity |  |
| Vendor credit rating (`vendor_credit_rating`) | The credit standing of a vendor on a five-step scale. It does not rate delivery or quality performance. | credit rating, creditworthiness, credit standing | supplier |
| Preferred vendor status (`preferred_supplier`) | Whether a vendor is preferred over other vendors that supply the same product. | preferred supplier, preferred vendor, primary source | supplier |
| Work order (`work_order`) | A production order to build a quantity of one product, with start, end and due dates. | WO, production order, manufacturing order, job |  |
| Work order due date (`work_order_due_date`) | The date a work order is due to be finished. It is a production due date, not a customer delivery date. | production due date, required completion date, target finish date | work_order |
| Production quantity stocked (`production_stocked_quantity`) | The quantity from a work order that was put into inventory. Order quantity minus scrapped quantity. | good quantity, completed quantity, yield, output quantity | work_order |
| Scrap reason (`scrap_reason`) | The recorded reason why a manufactured quantity was scrapped. | failure reason, defect type, reject reason, non-conformance reason | scrap_quality_context |
| Routing operation (`routing_operation`) | One step in the manufacturing sequence of a work order, performed at one work centre. | operation, routing step, process step, operation sequence | work_order |
| Inventory or manufacturing location (`location`) | A place where inventory is stored or a work centre where a manufacturing operation is performed. | work centre, work center, storage location, production area, station |  |
| Planned operation cost (`planned_cost`) | The estimated manufacturing cost of one routing operation. | estimated cost, budgeted cost, planned manufacturing cost | routing_operation |
| Actual operation cost (`actual_cost`) | The actual manufacturing cost recorded for one routing operation. | actual manufacturing cost, incurred cost, real cost | routing_operation |
| Actual resource hours (`resource_hours`) | The hours of resource recorded as actually used on one routing operation. | actual hours, hours worked, run time, processing time | routing_operation |
| Product document (`product_document`) | A maintenance, service or assembly document held for products. | manual, maintenance document, service guide, assembly instructions, work instruction |  |
| Document revision (`document_revision`) | The revision number of a product document. | revision, rev, version, issue | product_document |
| Document approval status (`document_approval_status`) | Whether a product document is pending approval, approved or obsolete. | approval status, document status, release status | product_document |
| Engineering change number (`engineering_change`) | The engineering change approval number recorded for a product document. | ECN, ECO, engineering change notice, change number | product_document |

## Metrics

| Metric | Label | Expression | Unit | Grain | Time field | Concept | Description | Synonyms | Example questions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sales_ordered_units` | Sales ordered units | `SUM(Sales_SalesOrderDetail.OrderQty)` | each | sales-order line | Sales_SalesOrderHeader.OrderDate | ordered_quantity | Sum of ordered units on sales-order lines. Not shipments, revenue or contract compliance. The order date and the customer are on the order header. The metric is computed on the line table. A filter on the order date or on a product attribute reaches the header or the product table through the declared joins from the line. A customer filter is two joins away, through the order header, and the compiler follows one declared join. | units ordered, ordered quantity, order volume, quantity ordered, sales volume | How many units of product 954 did customer 29641 order in 2024?, What is the ordered quantity by customer this year? |
| `production_requested_units` | Production requested units | `SUM(Production_WorkOrder.OrderQty)` | each | work order | StartDate | production_volume | Sum requested units for a selected product and period using StartDate. Keep separate from sales quantities. | production quantity, quantity to build, planned production quantity, build quantity | How many units were requested on work orders for this product in 2024? |
| `production_scrapped_units` | Production scrapped units | `SUM(Production_WorkOrder.ScrappedQty)` | each | work order | StartDate | scrap_quality_context | Sum scrapped units for a selected product and period using StartDate. Not a defect percentage. | scrap quantity, scrapped units, scrap volume, rejects | How many units were scrapped for this product in 2024? |
| `inventory_units` | Inventory units | `SUM(Production_ProductInventory.Quantity)` | each | product/location | none recorded | inventory_position | Sum of inventory units for a selected product across selected locations. The source records no business date for inventory (ModifiedDate is an audit timestamp), so the metric takes no date filter and is not a stock trend. | stock on hand, on-hand quantity, inventory level, stock quantity, QOH | How many units of this product are in stock at each location? |
| `sales_order_count` | Sales order count | `COUNT(DISTINCT Sales_SalesOrderHeader.SalesOrderID)` | count | sales order | OrderDate | sales_order | Count of distinct sales orders. Excludes duplicates across the header table. Time filter uses OrderDate. | number of orders, order count, orders placed, order volume | How many sales orders did this customer place in 2024? |
| `sales_line_revenue` | Sales line revenue | `SUM(Sales_SalesOrderDetail.LineTotal)` | currency amount (currency not recorded in the source) | sales-order line | Sales_SalesOrderHeader.OrderDate | revenue | Total sales revenue across order lines, as recorded in LineTotal. Not a margin or profit figure. The source records no currency. The order date and the customer are on the order header. This metric is computed on the line table only, so a date or customer filter needs a join that the metric compiler does not support. | revenue, sales value, sales amount, order value, turnover | What was the sales value of this product in 2024?, What is revenue by sales territory? |
| `average_unit_price` | Average sales unit price | `AVG(Sales_SalesOrderDetail.UnitPrice)` | currency amount per unit (currency not recorded in the source) | sales-order line | Sales_SalesOrderHeader.OrderDate | unit_price | Average unit price across all sales order lines before discount. Not a list price. The source records no currency. The order date and the customer are on the order header. This metric is computed on the line table only, so a date or customer filter needs a join that the metric compiler does not support. | average selling price, ASP, average price, mean unit price | What was the average selling price of this product in 2024? |
| `production_scrap_rate` | Production scrap rate | `production_scrapped_units / production_requested_units` | ratio | work order | StartDate | production_scrap | Ratio of scrapped units to requested units across work orders. Use StartDate for period filtering. Zero denominator returns NULL. Not a contract-quality determination. | scrap rate, scrap percentage, reject rate, scrap ratio | What was the scrap rate for this product in 2024? |
| `late_dispatch_orders` | Late dispatch orders | `COUNT(CASE WHEN Sales_SalesOrderHeader.ShipDate > Sales_SalesOrderHeader.DueDate THEN 1 END)` | count | sales order | OrderDate | delivery_lateness | Count of sales orders where the ship date is recorded after the due date. Each counted row is one order header. Zero in the pinned demo fixture; expression is correct for production data. | late shipments, orders shipped late, overdue shipments, shipped after due date | How many orders shipped after their due date for this customer? |
| `open_work_orders` | Open work orders | `COUNT(CASE WHEN Production_WorkOrder.EndDate IS NULL THEN 1 END)` | count | work order | StartDate | work_order_status | Count of work orders with no recorded completion date. Zero in the pinned demo fixture; expression is correct for live production data. | work in progress orders, WIP orders, unfinished work orders, outstanding work orders | How many work orders are still open for this product? |

## Postal address (`Person_Address`)

Street addresses of customers, employees and vendors. One row per address. Sales orders point here for the bill-to and the ship-to address.

Synonyms: address, delivery address, ship-to address, bill-to address, site address. Primary key: AddressID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `AddressID` | Address ID | identifier | postal_address |  | Unique key of one postal address. Sales orders reference it as the bill-to and the ship-to address. | address number, address key |  |
| `AddressLine1` | Street address line 1 | attribute |  |  | First line of the street address, usually the street and building number. | street address, address line 1, street |  |
| `AddressLine2` | Street address line 2 | attribute |  |  | Second line of the street address, such as a suite, unit or building. Empty for most addresses. | address line 2, suite, unit, building |  |
| `City` | City | dimension |  |  | Name of the city of the address. Use it to group orders by ship-to or bill-to city. | town, delivery city, ship-to city |  |
| `StateProvinceID` | State or province ID | identifier |  |  | Key of the state or province of the address. The state and province lookup is not included in this pack, so only the numeric key is available. | state, province, region |  |
| `PostalCode` | Postal code | attribute |  |  | Postal code of the street address, stored as text. | zip code, postcode, ZIP |  |
| `SpatialLocation` | Geographic coordinates | attribute |  |  | Latitude and longitude of the address, stored as an opaque geography value. This pack cannot run spatial queries on it. | geo-location, coordinates, latitude and longitude |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Bill of materials (`Production_BillOfMaterials`)

Components required to make bicycles and subassemblies. One row per parent assembly, component and start date. It gives the parent-to-component hierarchy and the quantity of each component per assembly. Top-level rows have no parent assembly.

Synonyms: BOM, product structure, parts list, components list, material list. Primary key: BillOfMaterialsID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `BillOfMaterialsID` | BOM line ID | identifier | bill_of_materials |  | Unique key of one bill-of-materials row, that is one component of one parent assembly for one period. | BOM ID, BOM line, structure line |  |
| `ProductAssemblyID` | Parent assembly product ID | identifier | product_identifier |  | Product that is built from the component, the parent assembly. Empty on the 103 top-level rows, which are exactly the rows at BOM level 0. | parent item, assembly, parent product, parent part |  |
| `ComponentID` | Component product ID | identifier | product_identifier |  | Product that is consumed as a component of the parent assembly. | component, child item, part, material, sub-component |  |
| `StartDate` | Component effective from | time |  |  | Date the component started being used in the assembly. | effective from, valid from, effectivity start |  |
| `EndDate` | Component effective to | time |  |  | Date the component stopped being used in the assembly. Empty while the component is still in use. | effective to, valid to, effectivity end, obsolete from |  |
| `UnitMeasureCode` | Component unit of measure | dimension | unit_of_measure |  | Unit of measure of the per-assembly quantity. Three codes occur here. Names come from the unit of measure lookup. Codes are padded to three characters. | UoM, unit, component unit | EA = Each; IN = Inch; OZ = Ounces |
| `BOMLevel` | BOM level | dimension |  |  | Depth of the component below the top of the product structure, as described by the source. Levels 0 to 4 occur. Level 0 rows have no parent assembly. | level, structure level, indenture level, depth |  |
| `PerAssemblyQty` | Component quantity per assembly | measure | component_quantity | component unit of measure per assembly (see UnitMeasureCode) | Quantity of the component needed to create one parent assembly, in the component unit of measure and for the effective period. Values range from 1 to 41. It is a design quantity, not a quantity ordered, issued or consumed. | quantity per, usage quantity, qty per assembly, component quantity, quantity required |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Business entity (party key) (`Person_BusinessEntity`)

Source of the shared key that connects vendors, stores and persons with address and contact information. One row per party. It holds no business attributes of its own.

Synonyms: party, party key, business partner. Primary key: BusinessEntityID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `BusinessEntityID` | Business entity ID | identifier |  |  | Shared party key. Vendors, stores and persons take their key from here, so the same number identifies the same party in each of those tables. | party ID, business partner ID |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Customer account (`Sales_Customer`)

Customer accounts that sales orders are placed against. One row per account. An account is an individual person (18,484 rows), a reseller store (701 rows), or a store with a named person (635 rows). Names are in the person and store tables.

Synonyms: customer, account, buyer, client, sold-to party. Primary key: CustomerID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `CustomerID` | Customer ID | identifier | customer_identifier |  | Unique key of one customer account in the AdventureWorks namespace. Sales orders reference it. The store ID links a business customer to its store. | customer number, customer account, account ID, client ID |  |
| `PersonID` | Person ID | identifier | contact_person |  | Key of the person on the account. This is an individual customer, or the named person when a store is also set. Empty when the account is a store without a named person. | contact, individual customer, contact person |  |
| `StoreID` | Store ID | identifier | reseller_store |  | Key of the reseller store the account belongs to. Empty for individual customers. | reseller, dealer, store account, business customer |  |
| `TerritoryID` | Sales territory ID | dimension | sales_territory |  | Key of the sales territory in which the customer is located. Territories 1 to 10 occur. The territory lookup is not included in this pack, so territory names are not available. | sales region, sales territory, region |  |
| `AccountNumber` | Customer account number | identifier | customer_identifier |  | Customer number assigned by the accounting system. The source computes it as AW followed by the zero-padded customer ID. | customer number, account no., customer account, customer code |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Product document (`Production_Document`)

Product maintenance, service and assembly documents, arranged as a folder tree. One row per folder or document. This data has 4 folder entries and 8 Word documents.

Synonyms: manual, maintenance document, service guide, assembly instructions, work instruction, technical document. Primary key: DocumentNode.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `DocumentNode` | Document node | identifier | product_document |  | Key of one document or folder. It is a position in the document tree stored as an opaque hierarchy value, not a readable path. | document ID, document key |  |
| `DocumentLevel` | Document tree level | attribute |  |  | Depth of the entry in the document tree, computed by the source from the document node. In this data 0 is the root, 1 is a folder and 2 is a document. | folder depth, hierarchy level |  |
| `Title` | Document title | attribute | product_document |  | Title of the document or folder, for example a maintenance or assembly guide title. | document name, manual name, guide name, title |  |
| `Owner` | Document owner (employee ID) | identifier |  |  | Employee who controls the document. The employee table is not included in this pack, so only the key is available. | document owner, document controller, author |  |
| `FolderFlag` | Folder or document flag | dimension |  |  | Marks an entry as a folder or a document. The source comment says 0 = this is a folder, 1 = this is a document. Observed data: the eight entries with 0 have the file extension .doc and the four entries with 1 have no file extension. This model does not resolve the difference. | is folder, entry type, folder indicator |  |
| `FileName` | File name | attribute |  |  | File name of the document including its extension. Folder entries repeat the folder title. | file, document file name |  |
| `FileExtension` | File extension | dimension |  |  | File extension that indicates the document type. Only .doc occurs in this data. Folder entries have no extension. | file type, file format, document format |  |
| `Revision` | Revision number | attribute | document_revision |  | Revision number of the document, stored as padded text. Values 0 to 8 occur. | rev, revision level, version, issue |  |
| `ChangeNumber` | Engineering change number | attribute | engineering_change |  | Engineering change approval number recorded for the document. The value 0 occurs on folders and on one document. The meaning of 0 is not recorded in the source. | ECN, ECO, engineering change notice, change approval number |  |
| `Status` | Document approval status | dimension | document_approval_status |  | Approval state of the document. Codes come from the source comment. Only 1 and 2 occur in this data. | approval status, document status, release status | 1 = Pending approval; 2 = Approved; 3 = Obsolete |
| `DocumentSummary` | Document summary | attribute |  |  | Abstract of the document in plain text. Empty for folder entries and for three of the eight documents. | abstract, summary, synopsis |  |
| `Document` | Document file content | attribute |  |  | The complete document file, stored as binary content. Folder entries have none. It cannot be queried as text here. Extracted text is held separately in this pack. | file content, attachment, document body |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier that the source database requires for file storage. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Inventory and manufacturing location (`Production_Location`)

Places where inventory is stored and work centres where manufacturing operations run. One row per location. Fourteen locations exist. Seven are storage or support areas and seven are work centres with a cost rate and capacity.

Synonyms: work centre, work center, storage location, production area, shop-floor location, station. Primary key: LocationID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `LocationID` | Location ID | identifier | location |  | Unique key of one inventory or manufacturing location. | location code, work centre ID, work center ID |  |
| `Name` | Location name | dimension | location |  | Name of the location. It is either a storage area, such as finished goods storage, or a work centre, such as frame welding or final assembly. | work centre, work center, storage location, production area, station, department |  |
| `CostRate` | Hourly cost rate | attribute |  | currency amount per hour (currency not recorded in the source) | Standard hourly cost of the manufacturing location. It is 0 for the seven storage and support locations. | hourly rate, work centre rate, standard hourly cost |  |
| `Availability` | Work capacity (hours) | attribute |  | hours | Work capacity of the manufacturing location in hours. The source does not state the period the hours cover. It is 0 for the seven storage and support locations. | capacity, available hours, work centre capacity |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Person (`Person_Person`)

People involved with the company. Employees, individual customers, store contacts and vendor contacts. One row per person. Names are fictional sample data and are personal values. Do not copy them into definitions.

Synonyms: contact, contact person, individual, individual customer, representative. Primary key: BusinessEntityID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `BusinessEntityID` | Person ID | identifier | contact_person |  | Unique key of one person, taken from the shared business-entity key. Customer accounts reference it. | contact ID, person key |  |
| `PersonType` | Person type | dimension |  |  | Primary type of the person. Codes come from the source comment. All six occur in this data. | contact type, role, party type | EM = Employee (non-sales); GC = General contact; IN = Individual (retail) customer; SC = Store contact; SP = Sales person; VC = Vendor contact |
| `NameStyle` | Name order style | attribute |  |  | Order in which the first and last name are stored. Only 0 occurs in this data. | name order, name format | 0 = Western style (first name, last name); 1 = Eastern style (last name, first name) |
| `Title` | Courtesy title | attribute |  |  | Courtesy title such as Mr. or Ms. Empty for most people. | salutation, honorific |  |
| `FirstName` | First name | attribute |  |  | First name of the person. A personal value. | given name, forename |  |
| `MiddleName` | Middle name | attribute |  |  | Middle name or middle initial of the person. A personal value. | middle initial |  |
| `LastName` | Last name | attribute |  |  | Last name of the person. A personal value. | surname, family name |  |
| `Suffix` | Name suffix | attribute |  |  | Surname suffix such as Sr. or Jr. Empty for most people. | suffix, generational suffix |  |
| `EmailPromotion` | Email promotion preference | dimension |  |  | Whether the contact wishes to receive e-mail promotions. Codes come from the source comment. | email opt-in, marketing consent, marketing preference | 0 = Does not wish to receive e-mail promotions; 1 = Wishes to receive e-mail promotions from AdventureWorks; 2 = Wishes to receive e-mail promotions from AdventureWorks and selected partners |
| `AdditionalContactInfo` | Additional contact information | attribute |  |  | Additional contact information about the person, stored as XML text. Filled for only 10 people. | other contact details, contact notes |  |
| `Demographics` | Demographics survey | attribute |  |  | Personal information such as hobbies and income, collected from online shoppers and stored as XML text. Personal data. It is not broken out into columns. | customer survey, demographic profile |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Product (`Production_Product`)

Products sold or used in the manufacturing of sold products. One row per product. It holds 504 products. 295 are salable finished goods and 239 are manufactured in-house. It carries the product number, standard cost, list price and planning values.

Synonyms: item, part, material, SKU, article, product master, item master. Primary key: ProductID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ProductID` | Product ID | identifier | product_identifier |  | Unique numeric key of one product in the AdventureWorks namespace. Order lines, inventory, work orders and bills of materials reference it. | item ID, material ID, part ID, product key |  |
| `Name` | Product name | dimension | product |  | Name of the product as on the product master. It usually includes model, color and size. | item name, item description, part name, material description, product description |  |
| `ProductNumber` | Product number | identifier | product_identifier |  | Unique product identification number in the AdventureWorks namespace, the code printed on documents. The same text in another supplier or customer namespace does not establish identity. | SKU, part number, item number, item code, P/N, article number |  |
| `MakeFlag` | Make or buy flag | dimension | make_or_buy |  | Whether the product is manufactured in-house or purchased. Codes come from the source comment. | make or buy, manufactured in-house, purchased part, bought-in part | 0 = Product is purchased; 1 = Product is manufactured in-house |
| `FinishedGoodsFlag` | Salable item flag | dimension |  |  | Whether the product is a salable item. Codes come from the source comment. | finished goods, FG, salable item, saleable product | 0 = Product is not a salable item; 1 = Product is salable |
| `Color` | Color | dimension |  |  | Product color. Nine colors occur. Empty for about half of the products. | colour, finish |  |
| `SafetyStockLevel` | Safety stock level | attribute | safety_stock | units (unit of measure not recorded in the source) | Minimum inventory quantity that should be kept for the product. It is a planning threshold on the product master, not a quantity on hand. | safety stock, buffer stock, minimum stock level, min stock |  |
| `ReorderPoint` | Reorder point | attribute | reorder_point | units (unit of measure not recorded in the source) | Inventory level that triggers a purchase order or a work order for the product. It is a planning threshold, not a quantity on hand or on order. | ROP, reorder level, replenishment point, order point |  |
| `StandardCost` | Standard cost | attribute | standard_cost | currency amount per unit (currency not recorded in the source) | Standard cost of one unit of the product. It is an internal costing value. It is not the list price, the unit price on an order line or a vendor price. It is 0 for 200 products. | std cost, standard unit cost, cost price, unit cost |  |
| `ListPrice` | List price | attribute | list_price | currency amount per unit (currency not recorded in the source) | Catalogue selling price of one unit before any discount. The price actually charged is the unit price on the sales-order line. It is 0 for 200 products, none of them salable. | catalogue price, catalog price, price list price, base price, selling price |  |
| `Size` | Size | dimension |  |  | Product size as text. Values mix numbers from 38 to 70 and the letter sizes S, M, L and XL. Empty for products without a size. | frame size, dimension, product size |  |
| `SizeUnitMeasureCode` | Size unit of measure | dimension | unit_of_measure |  | Unit of measure of the size. Only CM occurs. Empty where no unit is recorded. | size unit, size UoM | CM = Centimeter |
| `WeightUnitMeasureCode` | Weight unit of measure | dimension | unit_of_measure |  | Unit of measure of the weight. Two codes occur. Empty where no weight is recorded. | weight unit, weight UoM | G = Gram; LB = US pound |
| `Weight` | Weight | attribute |  | grams or US pounds (see WeightUnitMeasureCode) | Product weight in the unit given by the weight unit of measure, grams or US pounds. Convert units before comparing or adding weights. Empty for 299 products. | unit weight, net weight, mass |  |
| `DaysToManufacture` | Days to manufacture | attribute | manufacturing_lead_time | days | Number of days required to manufacture the product. Values 0, 1, 2 and 4 occur. It is a product master value, not the measured duration of a work order. | manufacturing lead time, production lead time, build time, make time |  |
| `ProductLine` | Product line | dimension | product_line |  | Product line of the product. Codes come from the source comment and are padded with a trailing space. Empty for 226 products. | line, range, product range | M = Mountain; R = Road; S = Standard; T = Touring |
| `Class` | Product class | dimension |  |  | Class of the product. Codes come from the source comment. The source does not say what is rated high, medium or low. Empty for 257 products. | class, grade, tier | H = High; L = Low; M = Medium |
| `Style` | Product style | dimension |  |  | Style of the product. Codes come from the source comment. Empty for 293 products. | style, gender, fit | M = Mens; U = Universal; W = Womens |
| `ProductSubcategoryID` | Product subcategory ID | identifier | product_subcategory |  | Subcategory the product belongs to. Empty for 209 products, which have no subcategory. | subcategory, product type |  |
| `ProductModelID` | Product model ID | identifier | product_model |  | Product model the product belongs to. Empty for 209 products, which have no model. | model, product family |  |
| `SellStartDate` | Sell start date | time |  |  | Date the product became available for sale. | launch date, available from, on-sale date |  |
| `SellEndDate` | Sell end date | time |  |  | Date the product was no longer available for sale. Empty for products still on sale. | end of sale, withdrawn from sale, available until |  |
| `DiscontinuedDate` | Discontinued date | time |  |  | Date the product was discontinued. Empty for every product in this data. | end of life, EOL, discontinuation date |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Product category (`Production_ProductCategory`)

Top-level product categories. One row per category. Four exist. Bikes, Components, Clothing and Accessories.

Synonyms: category, product group, product family. Primary key: ProductCategoryID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ProductCategoryID` | Product category ID | identifier | product_category |  | Unique key of one top-level product category. | category ID, category code |  |
| `Name` | Product category name | dimension | product_category |  | Name of the top-level product category. One of Bikes, Components, Clothing or Accessories. | category, product group, product family |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Product-to-document link (`Production_ProductDocument`)

Cross-reference that says which product documents apply to which products. One row per product and document. It has 32 links. A document without a link here has no recorded product; do not infer one.

Synonyms: document link, applicable documents, product documentation link. Primary key: ProductID, DocumentNode.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ProductID` | Product ID | identifier | product_identifier |  | Product the document applies to. | item, part, product |  |
| `DocumentNode` | Document node | identifier | product_document |  | Document that applies to the product. It refers to the document node of the product document table. | document ID, document key |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Product inventory by location (`Production_ProductInventory`)

Quantity of each product held at each inventory location at the source snapshot, with its shelf and bin. One row per product and location. It is a single snapshot, not a stock history, and not available-to-promise inventory.

Synonyms: stock, inventory, stock on hand, stock balance, on-hand inventory. Primary key: ProductID, LocationID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ProductID` | Product ID | identifier | product_identifier |  | Product whose stock is held at the location. | item, part, material |  |
| `LocationID` | Location ID | identifier | location |  | Inventory location where the stock is held. All fourteen locations hold stock in this data. | storage location, warehouse location, stock location |  |
| `Shelf` | Shelf | dimension |  |  | Storage compartment within the inventory location, held as a letter code. The text N/A is recorded on 290 rows where no shelf is given. | rack, shelf location, storage compartment |  |
| `Bin` | Bin | dimension |  |  | Storage container on a shelf within the inventory location, held as a number. Every row with shelf N/A has bin 0. | bin location, storage bin, container, slot |  |
| `Quantity` | Inventory quantity | measure | inventory_position | units (unit of measure not recorded in the source) | Quantity of the product held at one location at the source snapshot. Shelf and bin describe its position. It is not available-to-promise inventory, not a quantity on order, and not verified stock at any business date. | stock on hand, on-hand quantity, inventory level, stock quantity, QOH, stock balance |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the inventory row was last updated in the source system. The inventory metric uses it as its time field because the table has no snapshot date. Treat it as an update timestamp, not a stock-count date. |  |  |

## Product model (`Production_ProductModel`)

Product models. One row per model. 128 exist. A model groups product variants that differ by attributes such as size and color. A few models carry a catalogue description or manufacturing instructions as XML text.

Synonyms: model, product family, model range. Primary key: ProductModelID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ProductModelID` | Product model ID | identifier | product_model |  | Unique key of one product model. | model ID, model code |  |
| `Name` | Product model name | dimension | product_model |  | Name of the product model, shared by its size and color variants. | model, model name, product family |  |
| `CatalogDescription` | Catalogue description | attribute |  |  | Detailed product catalogue information for the model, stored as XML text. Filled for 6 of 128 models. | catalog description, product description, marketing description, product specification |  |
| `Instructions` | Manufacturing instructions | attribute |  |  | Manufacturing instructions for the model, stored as XML text. Filled for 9 of 128 models. | work instructions, assembly instructions, build instructions, process instructions |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Product subcategory (`Production_ProductSubcategory`)

Second-level product groupings inside a category, such as road bikes, brakes or helmets. One row per subcategory. 37 exist.

Synonyms: subcategory, product type, sub-group. Primary key: ProductSubcategoryID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ProductSubcategoryID` | Product subcategory ID | identifier | product_subcategory |  | Unique key of one product subcategory. | subcategory ID, subcategory code |  |
| `ProductCategoryID` | Product category ID | identifier | product_category |  | Top-level category that the subcategory belongs to. | category, parent category |  |
| `Name` | Product subcategory name | dimension | product_subcategory |  | Name of the product subcategory, for example a type of bike, component, clothing or accessory. | subcategory, product type, sub-group |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Vendor product terms (`Purchasing_ProductVendor`)

Which vendor supplies which product, with the supply terms recorded for that pair. Average lead time, usual price, last receipt, order quantity limits and unit of measure. One row per product and vendor. These are system records, not contract terms.

Synonyms: approved vendor list, supplier catalogue, vendor item, sourcing record, purchasing info record. Primary key: ProductID, BusinessEntityID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ProductID` | Product ID | identifier | product_identifier |  | Product that the vendor supplies. | item, part, purchased item, material |  |
| `BusinessEntityID` | Vendor ID | identifier | supplier_identifier |  | Vendor that supplies the product. It refers to the vendor key. | supplier ID, vendor number, supplier |  |
| `AverageLeadTime` | Average supplier lead time | measure | supplier_lead_time | days | Source-described average time in days between placing a purchase order with the vendor and receiving the product. Values range from 10 to 120 days. It is a recorded average, not a contract promise. A promised lead time in a contract may also count from a different start event. | lead time, supplier lead time, vendor lead time, average lead time, delivery lead time |  |
| `StandardPrice` | Vendor standard price | attribute | supplier_unit_price | currency amount per unit (currency not recorded in the source) | The vendor's usual selling price for the product. It is a reference price on the vendor-product record, not the unit price of a specific purchase order. | vendor price, supplier price, standard purchase price, usual price |  |
| `LastReceiptCost` | Last receipt cost | attribute |  | currency amount per unit (currency not recorded in the source) | The selling price when the product was last purchased from this vendor. | last purchase price, last cost, most recent price |  |
| `LastReceiptDate` | Last receipt date | time |  |  | Date the product was last received. The source comment says: Date the product was last received by the vendor. Observed data: all 460 rows hold a value, from 2022-07-21 to 2025-10-21, and 406 of them equal the row's ModifiedDate. This model does not resolve who received the product. | last delivery date, last goods receipt date, last received |  |
| `MinOrderQty` | Minimum order quantity | measure | minimum_order_quantity | vendor unit of measure (see UnitMeasureCode) | Smallest quantity that should be ordered from the vendor, read from the column name. Unresolved conflict. The source comment says maximum. Domain-owner review is required and the field is excluded from certified metrics. In the data it never exceeds the maximum order quantity. | MOQ, minimum order, min order qty, minimum order size |  |
| `MaxOrderQty` | Maximum order quantity | measure | maximum_order_quantity | vendor unit of measure (see UnitMeasureCode) | Largest quantity that should be ordered from the vendor, read from the column name. Unresolved conflict. The source comment says minimum. Domain-owner review is required and the field is excluded from certified metrics. In the data it is never below the minimum order quantity. | maximum order, max order qty, order ceiling, order cap |  |
| `OnOrderQty` | Quantity on order | measure | on_order_quantity | vendor unit of measure (see UnitMeasureCode) | Quantity of the product currently on order with the vendor. Empty for 305 of 460 rows. The source does not say whether empty means zero. | on order, open order quantity, outstanding order quantity, quantity on order |  |
| `UnitMeasureCode` | Vendor unit of measure | dimension | unit_of_measure |  | Unit of measure in which the vendor supplies the product. Seven codes occur. Names come from the unit of measure lookup. Note that CTN is named Container there. | UoM, purchase unit, order unit, pack unit | CAN = Canister; CS = Case; CTN = Container; DZ = Dozen; EA = Each; GAL = Gallon; PAK = Pack |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Purchase order line (`Purchasing_PurchaseOrderDetail`)

Products ordered on a purchase order. One row per purchase-order line. It holds the ordered, received, rejected and stocked quantities, the unit price and the expected receipt date. It has no actual receipt date.

Synonyms: PO line, PO line item, purchase order item, goods receipt line. Primary key: PurchaseOrderID, PurchaseOrderDetailID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `PurchaseOrderID` | Purchase order ID | identifier | purchase_order |  | Purchase order that the line belongs to. | PO, PO number, purchase order number |  |
| `PurchaseOrderDetailID` | Purchase order line ID | identifier |  |  | Key of one line of the purchase order. One line per purchased product. | PO line, line item, line number |  |
| `DueDate` | Expected receipt date | time | expected_receipt_date |  | Date the product on this line is expected to be received from the vendor. It is an expectation recorded on the order. It is not the actual receipt date, which this table does not hold, and not a contractual delivery promise. | expected delivery date, PO due date, need-by date, required-by date, delivery due date |  |
| `OrderQty` | Purchase ordered quantity | measure | purchase_ordered_quantity | units (unit of measure is not recorded on the purchase-order line) | Quantity of the product ordered from the vendor on this line. It is what was asked for. It is not the quantity received, rejected or stocked. | ordered quantity, PO quantity, quantity ordered, order qty |  |
| `ProductID` | Product ID | identifier | product_identifier |  | Product ordered from the vendor on this line. | item, part, purchased item, material |  |
| `UnitPrice` | Purchase unit price | measure | supplier_unit_price | currency amount per unit (currency not recorded in the source) | The vendor's selling price for one unit on this purchase-order line. It is the price on this order, which can differ from the vendor standard price. | purchase price, PO price, unit cost, price per unit |  |
| `LineTotal` | Purchase line total | measure |  | currency amount (currency not recorded in the source) | Value of the purchase-order line. The source computes it as ordered quantity times unit price and stores a snapshot. It is based on the ordered quantity, not the received quantity. It excludes tax and freight. | line amount, extended price, PO line value, line value |  |
| `ReceivedQty` | Received quantity | measure | received_quantity | units (unit of measure is not recorded on the purchase-order line) | Quantity actually received from the vendor for this line, before inspection. It includes units that were later rejected. It is below the ordered quantity on 380 of 8,845 lines and never above it. | goods received, quantity received, delivered quantity, receipt quantity, GR quantity |  |
| `RejectedQty` | Rejected quantity | measure | rejected_quantity | units (unit of measure is not recorded on the purchase-order line) | Quantity rejected during inspection, out of the received quantity. It is above zero on 563 lines. The source records no rejection reason for purchases. | rejects, inspection rejects, quantity rejected, non-conforming quantity, failed inspection |  |
| `StockedQty` | Stocked quantity | measure | stocked_quantity | units (unit of measure is not recorded on the purchase-order line) | Quantity accepted into inventory. The source computes it as received quantity minus rejected quantity and stores a snapshot. It is not the ordered quantity. | accepted quantity, quantity accepted into stock, quantity put away, quantity stocked |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Purchase order (`Purchasing_PurchaseOrderHeader`)

Purchase orders placed with vendors. One row per purchase order. It holds the vendor, status, order date, estimated ship date, shipping method and order-level amounts. It has 4,012 orders dated April 2022 to September 2025.

Synonyms: PO, purchase order, supplier order, procurement order. Primary key: PurchaseOrderID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `PurchaseOrderID` | Purchase order ID | identifier | purchase_order |  | Unique key of one purchase order placed with a vendor. | PO, PO number, purchase order number, order number |  |
| `RevisionNumber` | Purchase order revision number | attribute |  |  | Counter that tracks changes to the purchase order over time. It counts changes. It does not say what changed and it is not a contract amendment number. | PO revision, order revision, version |  |
| `Status` | Purchase order status | dimension | purchase_order_status |  | Current status of the purchase order. Codes come from the source comment. All four occur in this data. | PO status, order status, approval status | 1 = Pending; 2 = Approved; 3 = Rejected; 4 = Complete |
| `EmployeeID` | Buyer (employee ID) | identifier |  |  | Employee who created the purchase order. The employee table is not included in this pack, so only the key is available. | buyer, purchaser, purchasing agent |  |
| `VendorID` | Vendor ID | identifier | supplier_identifier |  | Vendor with whom the purchase order is placed. It refers to the vendor key. | supplier, vendor, seller, supplier ID |  |
| `ShipMethodID` | Shipping method ID | identifier | ship_method |  | Shipping method used for the purchase order. All five methods occur on purchase orders. | carrier, shipping method, delivery method |  |
| `OrderDate` | Purchase order date | time | order_date |  | Date the purchase order was created. It is the start of the order-to-receipt span. It is not the date the goods shipped or arrived. | PO date, date ordered, order placement date |  |
| `ShipDate` | Estimated vendor ship date | time |  |  | Estimated shipment date from the vendor. It is an estimate, per the source. It is not a confirmed dispatch and not the date the goods were received. | estimated ship date, expected dispatch date, vendor ship date |  |
| `SubTotal` | Purchase order subtotal | measure |  | currency amount (currency not recorded in the source) | Sum of the line totals of the purchase order, before tax and freight. | subtotal, net order value, goods value |  |
| `TaxAmt` | Purchase order tax amount | measure | tax_amount | currency amount (currency not recorded in the source) | Tax amount on the purchase order, held at order level. | tax, purchase tax, VAT, GST |  |
| `Freight` | Purchase order freight | measure | freight_charge | currency amount (currency not recorded in the source) | Shipping cost on the purchase order, held at order level. | freight, shipping cost, inbound freight, carriage, delivery charge |  |
| `TotalDue` | Purchase order total due | measure | order_total | currency amount (currency not recorded in the source) | Total due to the vendor for the purchase order. The source computes it as subtotal plus tax plus freight. It is an order amount, not a record of payment. | PO total, total due, amount due to vendor, order value |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Sales order line (`Sales_SalesOrderDetail`)

Products sold on a sales order. One row per sales-order line. It holds the ordered quantity, unit price, discount and line total. Order, due and ship dates and the customer are on the sales order. The model declares a derived join from ProductID to the product table. The source schema links order lines to products only through SpecialOfferProduct, which is outside the 25-table projection, so the join is derived from two source foreign keys.

Synonyms: order line, SO line, line item, order item, order position. Primary key: SalesOrderID, SalesOrderDetailID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `SalesOrderID` | Sales order ID | identifier | sales_order |  | Sales order that the line belongs to. | SO, order number, order ID |  |
| `SalesOrderDetailID` | Sales order line ID | identifier |  |  | Unique incremental key of one sales-order line. One line per product sold on the order. | order line, line item, SO line, line number |  |
| `CarrierTrackingNumber` | Carrier tracking number | attribute | tracking_number |  | Shipment tracking number supplied by the shipper. It is filled on every line of sales-person orders and empty on every line of online orders. | tracking number, waybill number, consignment number, shipment reference |  |
| `OrderQty` | Sales ordered quantity | measure | ordered_quantity | units (unit of measure not recorded in the source) | Count of product units ordered on one sales-order line. It is an order quantity. It is not a contractual minimum, a shipped quantity or proof of delivery. | ordered quantity, quantity ordered, order qty, units ordered, sales quantity |  |
| `ProductID` | Product ID | identifier | product_identifier |  | Product sold to the customer on this line. The model declares a derived join from this key to the product table, so a product attribute such as the product name can filter the line. | item, product, SKU, part |  |
| `SpecialOfferID` | Special offer ID | identifier |  |  | Promotional code applied to the line. Twelve codes occur and code 1 is on most lines. The special offer table is not included in this pack, so the meaning of each code is not recorded in the source available here. | promotion, promo code, special offer, deal |  |
| `UnitPrice` | Sales unit price | measure | unit_price | currency amount per unit (currency not recorded in the source) | Selling price of one unit on this sales-order line, before the line discount. It can differ from the product list price. It is not the standard cost. | selling price, price per unit, sales price, unit selling price |  |
| `UnitPriceDiscount` | Unit price discount | attribute | line_discount |  | Discount on the sales-order line. The source comment says: Discount amount. Observed data: values lie between 0 and 0.40 and are non-zero on 3,282 of 121,317 lines. On every line LineTotal equals OrderQty times UnitPrice times (1 minus this value). This model does not resolve the difference. | discount, price reduction |  |
| `LineTotal` | Sales line total | measure | revenue | currency amount (currency not recorded in the source) | Value of the sales-order line. The source computes it as unit price times (1 minus discount) times ordered quantity and stores a snapshot. It excludes tax and freight. It is an ordered value, not proof of delivery or payment. | line amount, extended price, net line amount, sales amount, line value |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Sales order (`Sales_SalesOrderHeader`)

Sales orders placed by customers. One row per sales order. It holds the customer, the order, due and ship dates, status, channel, addresses and order-level amounts. It has 31,465 orders dated 30 May 2022 to 29 June 2025. All are in status shipped.

Synonyms: customer order, SO, order, sales order header. Primary key: SalesOrderID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `SalesOrderID` | Sales order ID | identifier | sales_order |  | Unique numeric key of one sales order. | SO, order ID, order number, customer order |  |
| `RevisionNumber` | Sales order revision number | attribute |  |  | Counter that tracks changes to the sales order over time. It counts changes. It does not say what changed and it is not a contract amendment number. | order revision, version, change counter |  |
| `OrderDate` | Order date | time | order_date |  | Date the sales order was created. Sales metrics use it for period filters. It is not the due date or the ship date. | date ordered, order placement date, booking date, order entry date |  |
| `DueDate` | Order due date | time | delivery_promise |  | Date the order is due to the customer, as recorded on the sales order. It is the operational due date. It does not establish a negotiated delivery promise. A contract or amendment may promise another date and does not change this field. It is not the ship date or an actual receipt date. In this data it is 12 or more days after the order date. | due date, required date, requested delivery date, delivery due date, required-by date |  |
| `ShipDate` | Shipment date | time | shipment_date |  | Date the order was shipped to the customer. Shipment does not prove delivery or customer acceptance. In this data it is never after the due date. | ship date, dispatch date, despatch date, shipped date, date of dispatch |  |
| `Status` | Sales order status | dimension | sales_order_status |  | Current status of the sales order. Codes come from the source comment. Only 5 occurs in this data. | order status, order state, fulfilment status | 1 = In process; 2 = Approved; 3 = Backordered; 4 = Rejected; 5 = Shipped; 6 = Cancelled |
| `OnlineOrderFlag` | Online order flag | dimension | sales_channel |  | Whether the order was placed online by the customer or by a sales person. Codes come from the source comment. 27,659 orders are online and 3,806 are sales-person orders. | sales channel, online order, web order, order channel | 0 = Order placed by sales person; 1 = Order placed online by customer |
| `SalesOrderNumber` | Sales order number | identifier | sales_order |  | Readable sales order number. The source computes it as SO followed by the sales order ID. | SO number, order reference, order no. |  |
| `PurchaseOrderNumber` | Customer purchase order number | attribute | customer_po_reference |  | The customer's own purchase order number quoted on the sales order. It is a customer reference. It is not a key into this company's purchase orders. It is filled on every sales-person order and empty on every online order. | customer PO, customer PO number, buyer's order number, customer reference |  |
| `AccountNumber` | Accounting reference number | attribute |  |  | Financial accounting number reference on the order. It uses a different numbering scheme from the customer account number on the customer record. | accounting reference, ledger account, financial account number |  |
| `CustomerID` | Customer ID | identifier | customer_identifier |  | Customer account that placed the order. The source comment names Customer.BusinessEntityID, but the key and the join in this model use the customer ID. | customer, buyer, account, sold-to party |  |
| `SalesPersonID` | Sales person ID | identifier |  |  | Sales person who created the sales order. Empty on every online order. The sales person table is not included in this pack, so only the key is available. | sales rep, salesperson, account manager |  |
| `TerritoryID` | Sales territory ID | dimension | sales_territory |  | Key of the sales territory in which the sale was made. Territories 1 to 10 occur. The territory lookup is not included in this pack, so territory names are not available. | sales region, sales territory, region |  |
| `BillToAddressID` | Bill-to address ID | identifier | postal_address |  | Customer billing address of the order. It refers to the postal address table. | billing address, invoice address, bill-to |  |
| `ShipToAddressID` | Ship-to address ID | identifier | postal_address |  | Customer shipping address of the order. It refers to the postal address table. | shipping address, delivery address, ship-to, consignee address |  |
| `ShipMethodID` | Shipping method ID | identifier | ship_method |  | Shipping method used for the sales order. Only methods 1 and 5 occur on sales orders. | carrier, shipping method, delivery method |  |
| `CreditCardID` | Credit card ID | identifier |  |  | Key of the credit card used on the order. Empty on 1,131 orders. The credit card table is not included in this pack. | payment card, card on file |  |
| `CreditCardApprovalCode` | Credit card approval code | attribute |  |  | Approval code provided by the credit card company for the order payment. | authorisation code, authorization code, approval code |  |
| `CurrencyRateID` | Currency rate ID | identifier |  |  | Key of the currency exchange rate used for the order. Empty on 17,489 orders. The currency rate table is not included in this pack, so the order currency cannot be determined here. | exchange rate, FX rate, currency conversion |  |
| `SubTotal` | Sales order subtotal | measure |  | currency amount (currency not recorded in the source) | Sum of the line totals of the sales order, before tax and freight. | subtotal, net order value, goods value, order value before tax |  |
| `TaxAmt` | Sales order tax amount | measure | tax_amount | currency amount (currency not recorded in the source) | Tax amount on the sales order, held at order level. | tax, sales tax, VAT, GST |  |
| `Freight` | Sales order freight | measure | freight_charge | currency amount (currency not recorded in the source) | Shipping cost on the sales order, held at order level. | freight, shipping cost, delivery charge, carriage, outbound freight |  |
| `TotalDue` | Sales order total due | measure | order_total | currency amount (currency not recorded in the source) | Total due from the customer for the sales order. The source computes it as subtotal plus tax plus freight. It is an order amount, not a record of payment. | order total, total due, amount due, grand total, invoice total |  |
| `Comment` | Sales representative comment | attribute |  |  | Free-text comments from the sales representative. Empty on every order in this data. | order notes, remarks, comments |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Scrap reason (`Production_ScrapReason`)

Lookup of manufacturing failure reasons. One row per reason. Sixteen exist. Work orders with scrap point to one reason.

Synonyms: failure reason, defect type, reject reason, non-conformance reason, scrap code. Primary key: ScrapReasonID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ScrapReasonID` | Scrap reason ID | identifier | scrap_reason |  | Unique key of one manufacturing failure reason. | scrap code, reason code, defect code |  |
| `Name` | Scrap reason | dimension | scrap_reason |  | Description of the manufacturing failure that caused scrap, for example a failed paint process or a drill size that is too large. | failure reason, defect type, reject reason, non-conformance reason, cause of scrap |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Shipping method (`Purchasing_ShipMethod`)

Lookup of shipping companies and services. One row per method. Five exist. Both purchase orders and sales orders reference it.

Synonyms: carrier, shipping company, freight carrier, delivery service, mode of transport. Primary key: ShipMethodID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ShipMethodID` | Shipping method ID | identifier | ship_method |  | Unique key of one shipping method. | carrier ID, ship method code |  |
| `Name` | Shipping method name | dimension | ship_method |  | Name of the shipping company or service. | carrier, shipping company, freight carrier, delivery service, shipper |  |
| `ShipBase` | Minimum shipping charge | attribute |  | currency amount (currency not recorded in the source) | Minimum shipping charge for the method. It is a rate-card value, not the freight on an order. | base freight, minimum freight charge, base rate |  |
| `ShipRate` | Shipping rate per pound | attribute |  | currency amount per pound (currency not recorded in the source) | Shipping charge per pound of weight for the method. It is a rate-card value, not the freight on an order. | freight rate, rate per pound, per-lb rate |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Reseller store (`Sales_Store`)

Business customers that resell the company's products. One row per store. 701 exist. The store name is the customer name used for a business account.

Synonyms: reseller, dealer, retailer, store, business customer, distributor. Primary key: BusinessEntityID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `BusinessEntityID` | Store ID | identifier | reseller_store |  | Unique key of one reseller store, taken from the shared business-entity key. Customer accounts reference it as the store ID. | reseller ID, dealer ID, store number |  |
| `Name` | Store name | dimension | reseller_store |  | Name of the reseller store. Use it as the customer name of a business account. | customer name, reseller, dealer, retailer, account name, buyer |  |
| `SalesPersonID` | Assigned sales person ID | identifier |  |  | Sales person assigned to the store. The sales person table is not included in this pack, so only the key is available. | account manager, sales rep, assigned salesperson |  |
| `Demographics` | Store survey | attribute |  |  | Demographic information about the store, such as number of employees, annual sales and store type, stored as XML text. It is not broken out into columns. | store profile, store survey, reseller profile |  |
| `rowguid` | Row GUID | audit |  |  | Technical row identifier used by the source database for a replication sample. It has no business meaning. Do not use it to match documents or records. |  |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Unit of measure (`Production_UnitMeasure`)

Lookup of units of measure. One row per unit. 38 exist. Products, bills of materials and vendor product terms reference it.

Synonyms: UoM, UOM, unit, measurement unit. Primary key: UnitMeasureCode.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `UnitMeasureCode` | Unit of measure code | identifier | unit_of_measure |  | Three-character unit of measure code, padded with spaces, for example EA or LB. | UoM, UOM, unit code, unit |  |
| `Name` | Unit of measure name | dimension | unit_of_measure |  | Name of the unit of measure, for example Each, Case or US pound. | unit name, unit description |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Vendor (`Purchasing_Vendor`)

Companies from whom the company purchases parts or other goods. One row per vendor. 104 exist. It holds the name, account number, credit rating and the preferred and active flags.

Synonyms: supplier, vendor, seller, provider, source of supply. Primary key: BusinessEntityID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `BusinessEntityID` | Vendor ID | identifier | supplier_identifier |  | Unique key of one vendor, taken from the shared business-entity key. Purchase orders and vendor product terms reference it. | supplier ID, vendor number, supplier number, supplier code |  |
| `AccountNumber` | Vendor account number | identifier | supplier_identifier |  | Vendor account (identification) number, a readable text code for the vendor. | vendor account, supplier account number, vendor code |  |
| `Name` | Vendor name | dimension | supplier |  | Company name of the vendor. | supplier, supplier name, vendor, seller, provider |  |
| `CreditRating` | Vendor credit rating | dimension | vendor_credit_rating |  | Credit rating of the vendor. Codes come from the source comment. 1 is the best. It rates credit standing. It does not rate delivery or quality performance. | credit rating, creditworthiness, credit standing, credit score | 1 = Superior; 2 = Excellent; 3 = Above average; 4 = Average; 5 = Below average |
| `PreferredVendorStatus` | Preferred vendor status | dimension | preferred_supplier |  | Whether the vendor is preferred over other vendors supplying the same product. Codes come from the source comment. | preferred supplier, preferred vendor, primary source | 0 = Do not use if another vendor is available; 1 = Preferred over other vendors supplying the same product |
| `ActiveFlag` | Active vendor flag | dimension |  |  | Whether the vendor is actively used. Codes come from the source comment. Four vendors are no longer used. | active supplier, vendor status, inactive vendor | 0 = Vendor no longer used; 1 = Vendor is actively used |
| `PurchasingWebServiceURL` | Purchasing web service URL | attribute |  |  | Web address of the vendor's purchasing service, read from the column name. The source gives no description. Filled for 6 of 104 vendors. | supplier portal, ordering URL, e-procurement link |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Work order (`Production_WorkOrder`)

Production orders to build a quantity of one product. One row per work order. It holds the order, stocked and scrapped quantities, the start, end and due dates and the scrap reason. It has 72,591 work orders started June 2022 to June 2025. The pinned source has no column comments for this table. Meanings follow column names, keys, the computed expression and data, and need domain review.

Synonyms: WO, production order, manufacturing order, job, shop order. Primary key: WorkOrderID.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `WorkOrderID` | Work order ID | identifier | work_order |  | Unique key of one work order. | WO, work order number, production order number, job number |  |
| `ProductID` | Product ID | identifier | product_identifier |  | Product that the work order builds. | item, part, product built, assembly |  |
| `OrderQty` | Production order quantity | measure | production_volume | units (unit of measure not recorded in the source) | Requested product quantity on a work order, the quantity to build. It is not the customer sales-order quantity and not the quantity that reached stock. | quantity to build, planned production quantity, build quantity, work order quantity, lot size |  |
| `StockedQty` | Production quantity stocked | measure | production_stocked_quantity | units (unit of measure not recorded in the source) | Quantity built and put into inventory. The source computes it as order quantity minus scrapped quantity and stores a snapshot. It equals the order quantity on all but 729 work orders. It is not the stock on hand. | good quantity, completed quantity, yield, output quantity, quantity to stock |  |
| `ScrappedQty` | Scrapped quantity | measure | scrap_quality_context | units (unit of measure not recorded in the source) | Quantity scrapped on one work order. It is above zero on 729 work orders. A nonzero quantity does not by itself establish a contract-quality breach. | scrap, scrap quantity, scrapped units, rejects, defective units, waste |  |
| `StartDate` | Work order start date | time |  |  | Date the work order started. Production metrics use it for period filters. | production start date, start of production, start date |  |
| `EndDate` | Work order end date | time |  |  | Date the work order finished. An empty value would mean the work order is still open. Every work order in this data has one. It is after the due date on 22,790 work orders. | completion date, finish date, actual end date, closed date |  |
| `DueDate` | Work order due date | time | work_order_due_date |  | Date the work order is due to be finished. It is a production due date. It is not the sales-order due date and not a customer delivery promise. | production due date, required completion date, target finish date, scheduled completion |  |
| `ScrapReasonID` | Scrap reason ID | identifier | scrap_reason |  | Reason for the scrap on the work order. It is filled exactly on the 729 work orders that have scrap. | scrap reason, failure reason, defect code, reject reason |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Work order routing operation (`Production_WorkOrderRouting`)

Manufacturing operations of a work order. One row per work order, product and operation sequence. It holds the work centre, scheduled and actual dates, actual hours and planned and actual cost. 42,625 of the work orders have routing rows. The pinned source has no column comments for this table. Meanings follow column names, keys and data, and need domain review.

Synonyms: routing, operation, routing step, production operation, process step, shop-floor operation. Primary key: WorkOrderID, ProductID, OperationSequence.

| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `WorkOrderID` | Work order ID | identifier | work_order |  | Work order that the operation belongs to. | WO, work order number, production order |  |
| `ProductID` | Product ID | identifier | product_identifier |  | Product that the operation builds. It always equals the product of the work order in this data. | item, part, product built |  |
| `OperationSequence` | Operation sequence | identifier | routing_operation |  | Position of the operation in the manufacturing sequence of the work order. Values 1 to 7 occur. In this data each sequence number always runs at the same location. | operation number, routing step, op sequence, process step, operation |  |
| `LocationID` | Work centre (location ID) | identifier | location |  | Manufacturing location where the operation is performed. Seven work centres occur, locations 10 to 60. | work centre, work center, station, production area |  |
| `ScheduledStartDate` | Scheduled start date | time |  |  | Planned start date of the operation. It is the plan, not what happened. | planned start, scheduled start, plan start date |  |
| `ScheduledEndDate` | Scheduled end date | time |  |  | Planned end date of the operation. It is the plan, not what happened. The actual end is later than this date on 39,212 of 67,131 operations. | planned end, scheduled finish, planned completion |  |
| `ActualStartDate` | Actual start date | time |  |  | Date the operation actually started. Filled on every operation in this data. | actual start, real start date, started on |  |
| `ActualEndDate` | Actual end date | time |  |  | Date the operation actually ended. Filled on every operation in this data. | actual finish, actual completion, finished on |  |
| `ActualResourceHrs` | Actual resource hours | measure | resource_hours | hours | Hours of resource actually used on the operation, read from the column name. Values range from 1.0 to 4.1. The source does not say whether these are labour or machine hours. There is no planned-hours column. | actual hours, hours worked, run time, processing time, resource hours |  |
| `PlannedCost` | Planned operation cost | measure | planned_cost | currency amount (currency not recorded in the source) | Planned manufacturing cost of the operation. It is the estimate, not what was incurred. In this data it equals the actual cost on every operation. | estimated cost, budgeted cost, planned manufacturing cost, standard operation cost |  |
| `ActualCost` | Actual operation cost | measure | actual_cost | currency amount (currency not recorded in the source) | Actual manufacturing cost recorded for the operation. In this data it equals the planned cost on every operation, so the cost variance is always zero. | actual manufacturing cost, incurred cost, real cost |  |
| `ModifiedDate` | Last modified | audit |  |  | Date and time the row was last updated in the source system. It is a system audit timestamp, not a business event date. It can fall outside the transaction period. |  |  |

## Competency questions

| Question | Definitions | Joins |
| --- | --- | --- |
| For each vendor, how much did we order, how much was received, and how much was rejected at inspection? | Purchasing_Vendor.Name, Purchasing_PurchaseOrderDetail.OrderQty, Purchasing_PurchaseOrderDetail.ReceivedQty, Purchasing_PurchaseOrderDetail.RejectedQty | FK_PurchaseOrderDetail_PurchaseOrderHeader_PurchaseOrderID, FK_PurchaseOrderHeader_Vendor_VendorID |
| Which reseller stores had sales orders that shipped after the due date? | late_dispatch_orders, Sales_SalesOrderHeader.DueDate, Sales_SalesOrderHeader.ShipDate, Sales_Store.Name | FK_SalesOrderHeader_Customer_CustomerID, FK_Customer_Store_StoreID |
| How many units of a given product did a reseller store order in a calendar year? | sales_ordered_units, Sales_SalesOrderDetail.ProductID, Sales_SalesOrderHeader.OrderDate, Sales_Store.Name | FK_SalesOrderDetail_SalesOrderHeader_SalesOrderID, FK_SalesOrderHeader_Customer_CustomerID, FK_Customer_Store_StoreID |
| How many units were scrapped by scrap reason and product? | Production_WorkOrder.ScrappedQty, Production_ScrapReason.Name, Production_Product.Name | FK_WorkOrder_ScrapReason_ScrapReasonID, FK_WorkOrder_Product_ProductID |
| How much stock of a product is held at each inventory location? | inventory_units, Production_Location.Name, Production_Product.ProductNumber | FK_ProductInventory_Location_LocationID, FK_ProductInventory_Product_ProductID |
| Which products have total inventory below their reorder point or safety stock level? | inventory_units, Production_Product.ReorderPoint, Production_Product.SafetyStockLevel | FK_ProductInventory_Product_ProductID |
| What average lead time and order quantity limits are recorded for a product with each vendor? | Purchasing_ProductVendor.AverageLeadTime, Purchasing_ProductVendor.MinOrderQty, Purchasing_ProductVendor.MaxOrderQty, Purchasing_Vendor.Name, Production_Product.ProductNumber | FK_ProductVendor_Vendor_BusinessEntityID, FK_ProductVendor_Product_ProductID |
| Which work orders finished after their due date, by product? | Production_WorkOrder.EndDate, Production_WorkOrder.DueDate, Production_Product.Name | FK_WorkOrder_Product_ProductID |
| How do planned and actual cost and actual hours compare by work centre? | Production_WorkOrderRouting.PlannedCost, Production_WorkOrderRouting.ActualCost, Production_WorkOrderRouting.ActualResourceHrs, Production_Location.Name | FK_WorkOrderRouting_Location_LocationID |
| Which operations ended later than scheduled, by work centre? | Production_WorkOrderRouting.ScheduledEndDate, Production_WorkOrderRouting.ActualEndDate, Production_Location.Name | FK_WorkOrderRouting_Location_LocationID |
| What was the production scrap rate for a product in a period? | production_scrap_rate, Production_WorkOrder.ProductID, Production_WorkOrder.StartDate |  |
| Which components, and how many of each, does one unit of an assembly need? | Production_BillOfMaterials.ComponentID, Production_BillOfMaterials.PerAssemblyQty, Production_BillOfMaterials.UnitMeasureCode, Production_Product.Name | FK_BillOfMaterials_Product_ProductAssemblyID |
| Which maintenance or assembly documents apply to a product, and at which revision and approval status? | Production_Document.Title, Production_Document.Revision, Production_Document.Status, Production_Product.Name | FK_ProductDocument_Product_ProductID, FK_ProductDocument_Document_DocumentNode |
| What is sales line revenue for online orders compared with sales-person orders? | sales_line_revenue, Sales_SalesOrderHeader.OnlineOrderFlag | FK_SalesOrderDetail_SalesOrderHeader_SalesOrderID |
| What is the total due on purchase orders by status? | Purchasing_PurchaseOrderHeader.TotalDue, Purchasing_PurchaseOrderHeader.Status |  |
