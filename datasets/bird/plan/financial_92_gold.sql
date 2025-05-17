{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "CASE",
      "operator": "CASE",
      "type": "CHAR",
      "operands": [
        {
          "kind": "GREATER_THAN",
          "operator": ">",
          "type": "BOOLEAN",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "FLOAT"
            },
            {
              "kind": "INPUT_REF",
              "index": 1,
              "name": "$1",
              "type": "FLOAT"
            }
          ]
        },
        {
          "kind": "LITERAL",
          "value": "1996",
          "type": "CHAR",
          "nullable": false,
          "precision": 4
        },
        {
          "kind": "LITERAL",
          "value": "1995",
          "type": "CHAR",
          "nullable": false,
          "precision": 4
        }
      ]
    }
  ],
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [],
      "aggs": [
        {
          "operator": "AVG",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 0,
              "type": "FLOAT"
            }
          ],
          "type": "FLOAT",
          "name": null
        },
        {
          "operator": "AVG",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 1,
              "type": "FLOAT"
            }
          ],
          "type": "FLOAT",
          "name": null
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 12,
              "name": "$12",
              "type": "FLOAT"
            },
            {
              "kind": "INPUT_REF",
              "index": 11,
              "name": "$11",
              "type": "FLOAT"
            }
          ],
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "district",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}