{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 76,
      "name": "$76",
      "type": "VARCHAR"
    },
    {
      "kind": "CASE",
      "operator": "CASE",
      "type": "CHAR",
      "operands": [
        {
          "kind": "EQUALS",
          "operator": "=",
          "type": "BOOLEAN",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 21,
              "name": "$21",
              "type": "INTEGER"
            },
            {
              "kind": "LITERAL",
              "value": 1,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            }
          ]
        },
        {
          "kind": "LITERAL",
          "value": "YES",
          "type": "CHAR",
          "nullable": false,
          "precision": 3
        },
        {
          "kind": "CAST",
          "operator": "CAST",
          "type": "CHAR",
          "operands": [
            {
              "kind": "LITERAL",
              "value": "NO",
              "type": "CHAR",
              "nullable": false,
              "precision": 2
            }
          ]
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
            "value": "Jim Pavelec",
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
                "index": 77,
                "name": "$77",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 71,
                "name": "$71",
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
              "table": "rulings",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}