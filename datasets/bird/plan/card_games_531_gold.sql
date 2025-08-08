{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "VARCHAR"
    },
    {
      "column": 1,
      "type": "VARCHAR"
    },
    {
      "column": 2,
      "type": "VARCHAR"
    }
  ],
  "aggs": [],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 18,
          "name": "$18",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 48,
          "name": "$48",
          "type": "VARCHAR"
        },
        {
          "kind": "CASE",
          "operator": "CASE",
          "type": "VARCHAR",
          "operands": [
            {
              "kind": "EQUALS",
              "operator": "=",
              "type": "BOOLEAN",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 76,
                  "name": "$76",
                  "type": "VARCHAR"
                },
                {
                  "kind": "LITERAL",
                  "value": "Banned",
                  "type": "VARCHAR",
                  "nullable": false,
                  "precision": -1
                }
              ]
            },
            {
              "kind": "INPUT_REF",
              "index": 48,
              "name": "$48",
              "type": "VARCHAR"
            },
            {
              "kind": "LITERAL",
              "value": "NO",
              "type": "VARCHAR",
              "nullable": false,
              "precision": -1
            }
          ]
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 1,
                "name": "$1",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Allen Williams",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          "variableset": "[]",
          "id": "3",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 71,
                    "name": "$71",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 77,
                    "name": "$77",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "cards",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "legalities",
                  "id": "1",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}